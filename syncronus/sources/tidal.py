from __future__ import annotations

import json
import os
import random
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
        self._ensure_user_id_and_country()
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
            "client_id": self.client_id,
        }
        resp = requests.post(TIDAL_TOKEN_URL, data=data, timeout=15)
        if resp.status_code != 200:
            raise TidalAuthError(f"Failed to refresh token: {resp.text}")
        self._update_tokens(resp.json())

    def _get_song_id(self, isrc: str) -> str:
        """Get the Tidal track ID for a given ISRC."""
        self._ensure_token()
        self._ensure_user_id_and_country()

        # Search for the track by ISRC
        search_url = f"{TIDAL_API_BASE}/tracks"
        params = {
            "include": ["artists", "albums"],
            "filter[isrc]": isrc,
            "countryCode": self.user_country,
        }
        try:
            resp = self._get(search_url, params=params)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                raise TidalSongNotInRegionError(f"Track with ISRC {isrc} not found in your region.")
            else:
                raise RuntimeError(f"Failed to search for track with ISRC {isrc}: {e}")

        return resp["data"][0]["id"]

    def _create_playlist(self, playlist: Playlist) -> str:
        self._ensure_token()
        self._ensure_user_id_and_country()

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

    def _add_songs_to_playlist(self, playlist_id: str, songs: List[Song]) -> None:
        self._ensure_token()
        self._ensure_user_id_and_country()

        # Add songs to the playlist
        add_songs_url = f"{TIDAL_API_BASE}/playlists/{playlist_id}/relationships/items"
        songs_list = []
        for song in songs:
            try:
                song_id = self._get_song_id(song.isrc)
            except TidalSongNotInRegionError as e:
                logger.warning(f"Skipping song {song.isrc} not available in region: {e}")
                continue
            except Exception as e:
                logger.error(f"Failed to get song ID for {song.isrc}: {e}")
                continue
            # Prepare the list of songs to add
            songs_list.append(
                {
                    "type": "tracks",
                    "id": song_id,  # Use the Tidal track ID
                }
            )

        params = {"data": songs_list, "meta": {"positionBefore": playlist_id}}

        resp = self._post(add_songs_url, json=params)
        if resp.status_code == 201:
            logger.info(f"Added {len(songs)} songs to playlist {playlist_id}.")
        else:
            raise RuntimeError(f"Failed to add songs to playlist {playlist_id}: {resp.text}")

    def _get_user_id_and_country(self) -> str:
        """Get the user ID for the current user."""
        url = f"{TIDAL_API_BASE}/users/me"
        resp = self._get(url, {})
        return resp["data"]["id"], resp["data"]["attributes"]["country"]

    def _ensure_user_id_and_country(self) -> tuple[str, str]:
        """Ensure the user ID is set.  If not, fetch it from the API."""
        if self.user_id is None or self.user_country is None:
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

    def _get_tracks_from_url(self, url: str, max_pages: int = 1000, max_tracks: int = 50000) -> List[Song]:
        """
        Get tracks from a paginated URL with safety limits and proper error handling.

        Args:
            url: The initial URL to fetch tracks from
            max_pages: Soft limit on pages - will be exceeded if next_url exists (default: 1000)
            max_tracks: Hard limit on total tracks to prevent memory issues (default: 50,000)

        Returns:
            List of Song objects

        Raises:
            TidalEmptyPlaylistError: If the playlist is empty
            RuntimeError: If API errors occur or limits are exceeded
        """
        tracks: List[Song] = []
        current_url = url
        page_count = 0
        consecutive_failures = 0
        max_consecutive_failures = 3

        try:
            while current_url:
                page_count += 1

                # Hard limit check on total tracks (memory protection)
                if len(tracks) >= max_tracks:
                    logger.warning(f"Reached maximum track limit ({max_tracks}), stopping pagination")
                    break

                # Soft warning for high page count, but continue if next_url exists
                if page_count > max_pages:
                    logger.warning(f"Exceeded expected page limit ({max_pages}), continuing due to next_url (page {page_count})")

                try:
                    resp = self._get(url=current_url, max_retries=10)
                    consecutive_failures = 0  # Reset failure counter on success
                except requests.RequestException as e:
                    consecutive_failures += 1
                    logger.error(
                        f"Failed to fetch page {page_count} (failure {consecutive_failures}/{max_consecutive_failures}): {e}"
                    )

                    raise RuntimeError(f"Too many consecutive API failures ({consecutive_failures}), stopping pagination")

                    # Skip this page and try to continue
                    # logger.warning(f"Skipping page {page_count}, attempting to continue...")
                    # continue

                data = resp.get("data", [])
                links = resp.get("links", {})

                # Handle empty playlist on first page
                if page_count == 1 and not data:
                    raise TidalEmptyPlaylistError("Tidal playlist is empty.")
                # Process tracks on this page
                tracks_on_page = 0
                for item in data:
                    if item.get("type") == "tracks":
                        try:
                            song = self._song_from_api(item["id"])
                            tracks.append(song)
                            tracks_on_page += 1
                        except TidalSongNotInRegionError:
                            logger.warning(f"Song {item['id']} not available in region, skipping")
                            continue
                        except Exception as e:
                            logger.warning(f"Failed to process song {item.get('id', 'unknown')}: {e}")
                            continue
                # Check for next page
                next_url = links.get("next")
                if next_url:
                    # Handle both relative and absolute URLs
                    if next_url.startswith("/"):
                        current_url = f"{TIDAL_API_BASE}{next_url}"
                    else:
                        current_url = next_url
                else:
                    # No more pages
                    break
            # Check if we stopped due to track limit
            if len(tracks) >= max_tracks and current_url:
                logger.warning(f"Stopped at track limit ({max_tracks}) with more pages available")
            elif page_count > max_pages:
                logger.info(f"Completed pagination with {page_count} pages (exceeded soft limit of {max_pages})")

        except TidalEmptyPlaylistError:
            # Re-raise this specific exception
            raise
        except Exception as e:
            logger.error(f"Unexpected error while fetching tracks: {e}")
            raise RuntimeError(f"Failed to fetch tracks after {page_count} pages: {e}")

        logger.info(f"Successfully fetched {len(tracks)} tracks across {page_count} pages")
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

    def _calculate_retry_delay(self, response: requests.Response, retry_count: int, base_delay: float) -> float:
        """
        Calculate how long to wait before retrying based on response headers and attempt count.

        Args:
            response: The 429 response object
            retry_count: Current retry attempt (1-based)
            base_delay: Base delay in seconds for exponential backoff

        Returns:
            Number of seconds to wait
        """
        # Check for Retry-After header (preferred)
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                logger.warning(f"Invalid Retry-After header: {retry_after}")

        # Check for X-RateLimit-Reset header
        reset_timestamp = response.headers.get("X-RateLimit-Reset")
        if reset_timestamp:
            try:
                reset_time = int(reset_timestamp)
                wait_time = max(0, reset_time - int(time.time()))
                if wait_time > 0:
                    return wait_time
            except ValueError:
                logger.warning(f"Invalid X-RateLimit-Reset header: {reset_timestamp}")

        # Fallback to exponential backoff with jitter
        exponential_delay = base_delay * (2 ** (retry_count - 1))

        # Cap the delay at 60 seconds to prevent extremely long waits
        capped_delay = min(exponential_delay, 60)

        # Add jitter (±25%) to prevent thundering herd
        jitter = random.uniform(0.75, 1.25)
        final_delay = capped_delay * jitter

        return final_delay

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 5, **kwargs) -> Dict[str, Any]:
        """
        GET request with exponential backoff retry logic for rate limiting.

        Args:
            url: The URL to request
            params: Query parameters
            max_retries: Maximum number of retry attempts for rate limiting (default: 5)
            **kwargs: Additional arguments passed to requests.get

        Returns:
            JSON response as dictionary

        Raises:
            requests.HTTPError: For non-rate-limit HTTP errors
            RuntimeError: If max retries exceeded for rate limiting
        """
        if params is None:
            full_url = url
        else:
            full_url = f"{url}?{urlencode(params, doseq=True)}"

        retry_count = 0
        base_delay = 1  # Start with 1 second delay

        while retry_count <= max_retries:
            try:
                resp = requests.get(full_url, headers=self._headers(), timeout=15, **kwargs)
                # Handle rate limiting (429)
                if resp.status_code == 429:
                    if retry_count >= max_retries:
                        raise RuntimeError(f"Rate limit exceeded after {max_retries} retries for URL: {url}")

                    retry_count += 1
                    wait_time = self._calculate_retry_delay(resp, retry_count, base_delay)

                    # logger.warning(f"Rate limited (429) - retry {retry_count}/{max_retries} after {wait_time:.1f}s")
                    time.sleep(wait_time)
                    continue

                # Handle other HTTP errors (don't retry these)
                resp.raise_for_status()
                return resp.json()

            except requests.RequestException as e:
                # Don't retry on connection errors, timeouts, etc.
                # These are different from rate limiting and should fail fast
                raise requests.HTTPError(f"Request failed for URL {url}: {e}")

        # This should never be reached due to the retry_count check above,
        # but included for safety
        raise RuntimeError(f"Unexpected error: exceeded retry logic for URL: {url}")

    def _post(self, url: str, max_retries: int = 3, **kwargs) -> Dict[str, Any]:
        """
        POST request with retry logic for rate limiting.

        Args:
            url: The URL to request
            max_retries: Maximum number of retry attempts (default: 3, lower than GET)
            **kwargs: Additional arguments passed to requests.post

        Returns:
            JSON response as dictionary

        Raises:
            RuntimeError: For HTTP errors or max retries exceeded
        """
        retry_count = 0
        base_delay = 1

        while retry_count <= max_retries:
            resp = requests.post(url, headers=self._headers(), timeout=15, **kwargs)

            # Handle rate limiting
            if resp.status_code == 429:
                if retry_count >= max_retries:
                    raise RuntimeError(f"Rate limit exceeded after {max_retries} retries for POST {url}")

                retry_count += 1
                wait_time = self._calculate_retry_delay(resp, retry_count, base_delay)

                logger.warning(f"POST rate limited (429) - retry {retry_count}/{max_retries} after {wait_time:.1f}s")
                time.sleep(wait_time)
                continue

            # Handle other errors
            if resp.status_code >= 400:
                raise RuntimeError(f"TIDAL POST {url} failed with status {resp.status_code}: {resp.text}")

            return resp.json()

        raise RuntimeError(f"Unexpected error: exceeded retry logic for POST {url}")

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
            logger.warning("No valid access token found. Generating new auth URL...")
            # no cached credentials – need interactive login
            return self.generate_auth_url()

    def get_all_playlists(self) -> List[Playlist]:
        self._ensure_token()
        self._ensure_user_id_and_country()

        playlists: List[Playlist] = []

        url = f"{TIDAL_API_BASE}/playlists"

        resp = self._get(url, {"filter[r.owners.id]": self.user_id, "countryCode": self.user_country})
        for data in tqdm(resp["data"]):
            logger.debug(f"Found playlist: {data['attributes']['name']} (ID: {data['id']})")
            playlists.append(self._playlist_from_api(data["id"]))
        return playlists

    def generate_playlist(self, seed: list) -> Playlist:
        pass
        # return Playlist()

    def sync_playlists(self, playlist: Playlist) -> None:

        self._ensure_token()
        self._ensure_user_id_and_country()
        if playlist.songs == []:
            raise TidalEmptyPlaylistError("Cannot sync an empty playlist.")

        # Create the playlist on Tidal
        playlist_id = self._create_playlist(playlist)

        # Add songs to the playlist
        self._add_songs_to_playlist(playlist_id, playlist.songs)
        logger.info(f"Synced playlist {playlist.name} with ID {playlist_id} on Tidal.")
