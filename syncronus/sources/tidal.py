from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv
from tqdm import tqdm
import secrets
import hashlib
import base64

# Local models
from syncronus.sources.base import BaseClient, Song, Playlist
from syncronus.logger import get_logger

logger = get_logger(__name__)

TIDAL_AUTH_URL = "https://login.tidal.com/authorize"
TIDAL_TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
TIDAL_API_BASE = "https://openapi.tidal.com/v2"

DEFAULT_CACHE_PATH = Path(".cache/tidal_token.json")


class TidalAuthError(Exception):
    """Custom exception for Tidal authentication errors."""


class TidalSongNotInRegionError(RuntimeError):
    """Custom exception for Tidal song not in region errors."""


class TidalEmptyPlaylistError(RuntimeError):
    """Custom exception for Tidal empty playlist errors."""


class TidalClient(BaseClient):
    """Plain‑requests Tidal Web API client with on‑disk token cache."""

    def __init__(
        self,
        *,
        client_id: str = os.getenv("TIDAL_CLIENT_ID"),
        client_secret: str = os.getenv("TIDAL_CLIENT_SECRET"),
        cache_path: Path = DEFAULT_CACHE_PATH,
        scope: List[str] = None,
        redirect_uri: str = "https://localhost:8080/callback",
    ):
        load_dotenv()  # Best‑effort env load

        self.client_id = client_id or os.getenv("TIDAL_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("TIDAL_CLIENT_SECRET")
        self.redirect_uri = redirect_uri
        self.scope = scope or ["playlists.read", "playlists.write", "entitlements.read", "user.read"]

        if not (self.client_id and self.client_secret):
            raise TidalAuthError("TIDAL_CLIENT_ID / TIDAL_CLIENT_SECRET missing – set env vars or pass explicitly.")

        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: float = 0.0
        self.user_id = None
        self.verifier = None
        self.user_country = None
        self._load_cached_tokens()

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def _make_code_verifier(self) -> str:
        """Return 43-128 random characters suitable for PKCE."""
        # 32 bytes ⇒ 43 base64url chars (without padding).   Increase bytes for longer strings.
        return secrets.token_urlsafe(32)

    def _to_code_challenge(self, verifier: str) -> str:
        """Return the S256 code-challenge for a given verifier."""
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def generate_auth_url(self, state: str | None = None) -> tuple[str, str]:
        verifier = self._make_code_verifier()
        challenge = self._to_code_challenge(verifier)

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": " ".join(self.scope),
        }
        if state:
            params["state"] = state

        # Add the verifier to the cache
        cache = {
            "verifier": verifier,
        }
        # Read the cache file if it exists and update the verifier
        if self.cache_path.exists():
            with open(self.cache_path, "r") as f:
                cache.update(json.load(f))

        # Write the updated cache to the file
        with open(self.cache_path, "w") as f:
            json.dump(cache, f)
        self.verifier = verifier
        return f"{TIDAL_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> None:
        """Exchange the *authorization code* for **access** & **refresh** tokens.

        Call this **exactly once** after the user authorizes the app in the
        browser.  The refresh token is cached so future sessions can skip this.
        """
        # Load the verifier from the cache
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": self.verifier,
        }
        resp = requests.post(TIDAL_TOKEN_URL, data=data, timeout=15)
        if resp.status_code != 200:
            raise TidalAuthError(f"Failed to exchange code: {resp.text}")
        payload = resp.json()
        self._update_tokens(payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_cached_tokens(self) -> None:
        if self.cache_path.exists():
            data = json.loads(self.cache_path.read_text())
            self.refresh_token = data.get("refresh_token")
            self.access_token = data.get("access_token")
            self.expires_at = data.get("expires_at", 0.0)

    def _save_cached_tokens(self) -> None:
        data = {
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "verifier": self.verifier,
            "expires_at": self.expires_at,
        }
        self.cache_path.write_text(json.dumps(data))

    def _update_tokens(self, payload: Dict[str, Any]) -> None:
        self.access_token = payload["access_token"]
        self.expires_at = time.time() + payload["expires_in"] - 60  # margin
        if "refresh_token" in payload and payload["refresh_token"]:
            self.refresh_token = payload["refresh_token"]
        self._save_cached_tokens()

    def _ensure_token(self) -> None:
        if not self.access_token or time.time() >= self.expires_at:
            if not self.refresh_token:
                raise TidalAuthError("No refresh token – call `generate_auth_url` then `exchange_code`.")
            self._refresh_access_token()

    def _refresh_access_token(self) -> None:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        resp = requests.post(TIDAL_TOKEN_URL, data=data, timeout=15)
        if resp.status_code != 200:
            raise TidalAuthError(f"Failed to refresh token: {resp.text}")
        self._update_tokens(resp.json())

    # -------------------------Helpers--------------------------------

    def _get_user_id_and_country(self) -> str:
        """Get the user ID for the current user."""
        url = f"{TIDAL_API_BASE}/users/me"
        resp = self._get(url, {})
        return resp["data"]["id"], resp["data"]["attributes"]["country"]

    def _ensure_user_id_and_country(self) -> str:
        """Ensure the user ID is set.  If not, fetch it from the API."""
        if self.user_id is None:
            self.user_id, self.user_country = self._get_user_id_and_country()
        return self.user_id, self.user_country

    def _song_from_api(self, id: str) -> Song:
        """
        Get tracks from a given URL.
        based on the Tidal API documentation,
        https://developer.tidal.com/apiref?spec=catalogue-v2&ref=get-single-track&at=THIRD_PARTY
        """
        try:
            resp = self._get(f"{TIDAL_API_BASE}/tracks/{id}", {"include": ["artists", "albums"], "countryCode": self.user_country})
            data = resp["data"]
            items = resp["included"]
            artists = []
            album = None

            for item in items:
                if item["type"] == "albums":
                    album = item["attributes"]["title"]
                elif item["type"] == "artists":
                    artists.append(item["attributes"]["name"])
            song = Song(
                isrc=data["attributes"]["isrc"],
                title=data["attributes"]["title"],
                artist=artists,
                album=album,
                duration_ms=data["attributes"]["duration"],
            )
            return song
        except TidalSongNotInRegionError:
            raise TidalSongNotInRegionError(f"Tidal song not available in your region: {id}")
        except Exception as e:
            raise Exception(f"Failed to parse track {id}: {e}")

    def _get_tracks_from_url(self, url: str) -> List[Song]:
        tracks: List[Song] = []
        resp = self._get(url, {"countryCode": self.user_country})
        data = resp["data"]
        links = resp.get("links", {})
        if data == []:
            raise TidalEmptyPlaylistError("Tidal playlist is empty.")

        is_next = True
        counter = 0
        while is_next:
            for item in data:
                if item["type"] == "tracks":
                    song = self._song_from_api(item["id"])
                    tracks.append(song)
            if links.get("next", None) is None:
                is_next = False
                break
            else:
                url = f"{TIDAL_API_BASE}{links["next"]}"
                resps = self._get(url)
                data = resps["data"]
                links = resps.get("links", {})
                counter += 1
        return tracks

    def _playlist_from_api(self, id: str) -> Playlist:
        """
        Get a playlist from a given URL.
        based on the Tidal API documentation,
        https://developer.tidal.com/apiref?spec=user-playlist-v2&ref=get-single-playlist&at=THIRD_PARTY
        """
        url = f"{TIDAL_API_BASE}/playlists/{id}"
        resp = self._get(url, {"countryCode": self.user_country})
        data = resp["data"]
        try:
            songs = self._get_tracks_from_url(f"{TIDAL_API_BASE}/playlists/{id}/relationships/items")
        except TidalEmptyPlaylistError as e:
            songs = []
            logger.warning(f"Empty playlist {id}: {e}")
        cover_image_list = resp["data"]["attributes"].get("imageLinks", {})
        if cover_image_list != {}:
            cover_image_path = max(cover_image_list, key=lambda x: x["meta"]["width"]).get("href")
        else:
            cover_image_path = None
        try:
            return Playlist(
                id=data["id"],
                name=data["attributes"]["name"],
                description=data["attributes"].get("description", ""),
                songs=songs,
                service="tidal",
                url=f"https://listen.tidal.com/playlist/{data['id']}",
                cover_image_path=cover_image_path,
            )
        except Exception as e:
            raise Exception(f"Failed to parse playlist {data}: {e}")

    # ------------------------- HTTP wrappers -------------------------
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        while True:
            if params is None:
                urls = url
            else:
                urls = f"{url}?{urlencode(params, doseq=True)}"
            resp = requests.get(urls, headers=self._headers(), timeout=15, **kwargs)
            if resp.status_code != 429:
                resp.raise_for_status()  # raise for any 4xx/5xx except 429
                return resp.json()

            # ── 429 handling ────────────────────────────────────────────
            retry_after = resp.headers.get("Retry-After")
            reset_ts = resp.headers.get("X-RateLimit-Reset")
            if retry_after is not None:
                wait = int(retry_after)
            elif reset_ts is not None:
                wait = max(0, int(reset_ts) - int(time.time()))
            else:
                wait = 5  # sensible fallback

            time.sleep(wait + 0.5)

    def _post(self, url: str, **kwargs) -> Dict[str, Any]:
        resp = requests.post(url, headers=self._headers(), timeout=15, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"TIDAL POST {url} failed: {resp.text}")
        return resp.json()

    # ------------------------------------------------------------------
    # Public API (BaseClient overrides)
    # ------------------------------------------------------------------

    def authenticate(self) -> Optional[str]:
        """Smart auth helper for UI buttons.

        Returns
        -------
        * **``None``** if authentication is already valid (token cached and
          refreshed).  The caller can proceed with API calls immediately.
        * **``str``** – a Spotify authorization URL.  The caller should direct
          the user to this URL to complete the one‑time grant.  After the user
          authorises and you capture the ``?code`` parameter, call
          :pymeth:`exchange_code` once – the next :pymeth:`authenticate` call
          will then return ``None``.
        """
        try:
            self._ensure_token()  # may refresh silently
            return None
        except TidalAuthError:
            # no cached credentials – need interactive login
            return self.generate_auth_url()

    def get_all_playlists(self) -> List[Playlist]:
        self._ensure_token()
        self._ensure_user_id_and_country()

        playlists: List[Playlist] = []

        url = f"{TIDAL_API_BASE}/playlists"

        resp = self._get(url, {"filter[r.owners.id]": self.user_id, "countryCode": self.user_country})
        for data in tqdm(resp["data"]):
            playlists.append(self._playlist_from_api(data["id"]))
        return playlists

    def create_playlist(self, playlist: Playlist) -> Playlist:
        self._ensure_token()
        self._ensure_user_id()

        # Create a new playlist
        create_playlists_url = f"{TIDAL_API_BASE}/playlists"

        params = {
            "data": {
                "type": "playlists",
                "attributes": {"name": playlist.name, "description": playlist.description, "privacy": "PRIVATE"},
            }
        }

        resp = self._post(create_playlists_url, params=params)

        playlist_id = resp["data"]["id"]

        return playlist_id

    def generate_playlist(self, seed: list) -> Playlist:
        pass
        # return Playlist()

    def sync_playlists(self, playlists: List[Playlist]) -> None:
        pass
