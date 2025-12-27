from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv
from syncronus.logger import get_logger

# Local models
from syncronus.sources.base import BaseClient, Song, Playlist

logger = get_logger(__name__)

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

DEFAULT_CACHE_PATH = Path(".cache/spotify_token.json")


class SpotifyAuthError(RuntimeError):
    """Raised when authentication/authorization fails."""


class SpotifySongNotInRegionError(RuntimeError):
    """Raised when a song is not available in the user's region."""


class SpotifyClient(BaseClient):
    """Plain‑requests Spotify Web API client with on‑disk token cache."""

    def __init__(
        self,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = "http://127.0.0.1:8888/callback",
        scope: Optional[List[str]] = None,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
    ) -> None:
        load_dotenv()  # Best‑effort env load

        self.client_id = client_id or os.getenv("SPOTIFY_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("SPOTIFY_CLIENT_SECRET")
        self.redirect_uri = redirect_uri
        self.scope = scope or [
            "playlist-read-private",
            "playlist-modify-public",
            "playlist-modify-private",
        ]

        if not (self.client_id and self.client_secret):
            raise SpotifyAuthError("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET missing – set env vars or pass explicitly.")

        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: float = 0.0

        self._load_cached_tokens()
        # self._ensure_token()

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------
    def generate_auth_url(self, state: str | None = None) -> str:
        """Return the URL that the user must visit to grant permissions."""

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scope),
        }
        if state:
            params["state"] = state
        return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> None:
        """Exchange the *authorization code* for **access** & **refresh** tokens.

        Call this **exactly once** after the user authorizes the app in the
        browser.  The refresh token is cached so future sessions can skip this.
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        resp = requests.post(SPOTIFY_TOKEN_URL, data=data, timeout=15)
        if resp.status_code != 200:
            raise SpotifyAuthError(f"Failed to exchange code: {resp.text}")
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
                raise SpotifyAuthError("No refresh token – call `generate_auth_url` then `exchange_code`.")
            self._refresh_access_token()

    def _refresh_access_token(self) -> None:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        resp = requests.post(SPOTIFY_TOKEN_URL, data=data, timeout=15)
        if resp.status_code != 200:
            raise SpotifyAuthError(f"Failed to refresh token: {resp.text}")
        self._update_tokens(resp.json())

    def _get_tracks_from_url(self, url: str) -> List[Song]:
        self._ensure_token()
        tracks: List[Song] = []
        resp = self._get(url)

        for item in resp["items"]:
            try:
                track = self._song_from_api(item["track"])
                tracks.append(track)
            except SpotifySongNotInRegionError:
                logger.debug("Song is not available in your region.")
                continue
            except Exception as e:
                raise Exception(f"Failed to parse track {item}: {e}")
        return tracks

    def _song_from_api(item: Dict[str, Any]) -> Song:
        if item is None:
            raise SpotifySongNotInRegionError("Song not available in your region.")
        else:
            return Song(
                isrc=item["external_ids"]["isrc"],
                title=item["name"],
                artist=[a["name"] for a in item["artists"]],
                album=item["album"]["name"],
                duration_ms=item["duration_ms"],
            )

    def _playlist_from_api(self, item: Dict[str, Any]) -> Playlist:
        try:
            return Playlist(
                id=item["id"],
                name=item["name"],
                description=item.get("description", ""),
                songs=self._get_tracks_from_url(item.get("tracks").get("href")),  # lazily loaded on demand
                cover_image_path=item.get("images", [])[0].get("url"),
                url=item["external_urls"].get("spotify"),
            )
        except Exception as e:
            raise Exception(f"Failed to parse playlist {item}: {e}")

    # ------------------------- HTTP wrappers -------------------------
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    def _get(self, url: str, **kwargs) -> Dict[str, Any]:
        resp = requests.get(url, headers=self._headers(), timeout=15, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"Spotify GET {url} failed: {resp.text}")
        return resp.json()

    def _post(self, url: str, **kwargs) -> Dict[str, Any]:
        resp = requests.post(url, headers=self._headers(), timeout=15, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"Spotify POST {url} failed: {resp.text}")
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
        except SpotifyAuthError:
            # no cached credentials – need interactive login
            return self.generate_auth_url()

    def get_all_playlists(self) -> List[Playlist]:
        self._ensure_token()
        playlists: List[Playlist] = []
        url = f"{SPOTIFY_API_BASE}/me/playlists?limit=50"
        while url:
            resp = self._get(url)
            for item in resp["items"]:
                playlists.append(self._playlist_from_api(item))
            url = resp.get("next")
        return playlists

    def sync_playlists(self, playlists):
        pass

    def create_playlist(
        self,
        name: str,
        *,
        description: str = "",
        public: bool = False,
        songs: Optional[List[Song]] = None,
    ) -> Playlist:
        self._ensure_token()
        user_id = self._get(f"{SPOTIFY_API_BASE}/me")["id"]
        payload = {
            "name": name,
            "public": public,
            "description": description,
        }
        created = self._post(f"{SPOTIFY_API_BASE}/users/{user_id}/playlists", json=payload)
        playlist_id = created["id"]

        if songs:
            uris = [s.id for s in songs]
            # Spotify expects track URIs such as "spotify:track:xxxx".
            self._post(f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks", json={"uris": uris})

        return self._playlist_from_api(created)

    def generate_playlist(
        self,
        name: str,
        genres: List[str],
        *,
        description: str = "Generated by Streamlit Playlist Copier",
        total_songs: int = 25,
        public: bool = False,
    ) -> Playlist:
        self._ensure_token()
        # Spotify allows up to 5 seed genres.
        genres = genres[:5]
        params = {
            "seed_genres": ",".join(genres),
            "limit": total_songs,
        }
        recs = self._get(f"{SPOTIFY_API_BASE}/recommendations", params=params)
        songs = [self._song_from_api(t) for t in recs["tracks"]]
        return self.create_playlist(name, description=description, public=public, songs=songs)
