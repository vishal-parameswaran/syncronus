"""Tidal API client implementation."""

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

from syncronus.logger import get_logger
from syncronus.sources.base import BaseClient, Song, Playlist
from syncronus.sources.oauth2 import OAuth2Client, OAuth2Error


logger = get_logger(__name__)

TIDAL_AUTH_URL = "https://login.tidal.com/authorize"
TIDAL_TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
TIDAL_API_BASE = "https://openapi.tidal.com/v2"

DEFAULT_CACHE_PATH = Path(".cache/tidal_token.json")


class TidalAuthError(OAuth2Error):
    """Raised when Tidal authentication errors occur."""


class TidalSongNotInRegionError(RuntimeError):
    """Raised when a Tidal song is not available in the user's region."""


class TidalEmptyPlaylistError(RuntimeError):
    """Raised when a Tidal playlist is empty."""


class TidalOAuth2Client(OAuth2Client):
    """OAuth2 client specifically for Tidal with PKCE support."""

    def __init__(self, *args, **kwargs):
        # Tidal-specific state
        self.user_id: Optional[str] = None
        self.user_country: Optional[str] = None
        super().__init__(*args, **kwargs)

        # Load user info from cache if available
        self._load_user_info()

    @property
    def auth_url(self) -> str:
        return TIDAL_AUTH_URL

    @property
    def token_url(self) -> str:
        return TIDAL_TOKEN_URL

    @property
    def service_name(self) -> str:
        return "Tidal"

    def _requires_client_secret_for_refresh(self) -> bool:
        """Tidal does NOT require client_secret for token refresh (only client_id)."""
        return False

    def _requires_client_secret_for_exchange(self) -> bool:
        """Tidal requires client_secret even with PKCE."""
        return False

    def _save_cached_tokens(self) -> None:
        """Save tokens and user info to cache."""
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "user_id": self.user_id,
            "user_country": self.user_country,
        }

        # Preserve verifier if it exists
        if self.code_verifier:
            data["verifier"] = self.code_verifier

        self.cache_path.write_text(json.dumps(data))

    def _load_user_info(self) -> None:
        """Load user info from cache if available."""
        if not self.cache_path.exists():
            return

        try:
            data = json.loads(self.cache_path.read_text())
            self.user_id = data.get("user_id")
            self.user_country = data.get("user_country")
        except (json.JSONDecodeError, OSError):
            pass


class TidalClient(BaseClient):
    """Tidal Web API client with OAuth2 PKCE authentication and token caching."""

    def __init__(
        self,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: str = "https://localhost:8080/callback",
        scope: Optional[List[str]] = None,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
    ) -> None:
        """
        Initialize Tidal client.

        Args:
            client_id: Tidal client ID (or from TIDAL_CLIENT_ID env var)
            client_secret: Tidal client secret (or from TIDAL_CLIENT_SECRET env var)
            redirect_uri: OAuth2 redirect URI
            scope: List of permission scopes
            cache_path: Path to cache tokens
        """
        load_dotenv()  # Load environment variables

        client_id = client_id or os.getenv("TIDAL_CLIENT_ID")
        client_secret = client_secret or os.getenv("TIDAL_CLIENT_SECRET")

        if not (client_id and client_secret):
            raise TidalAuthError("TIDAL_CLIENT_ID and TIDAL_CLIENT_SECRET must be set")

        # Initialize OAuth2 client with PKCE
        self.oauth = TidalOAuth2Client(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope
            or [
                "playlists.read",
                "playlists.write",
                "entitlements.read",
                "user.read",
            ],
            cache_path=Path(cache_path),
            use_pkce=True,  # Tidal requires PKCE
        )

    # ------------------------------------------------------------------
    # OAuth2 flow (delegated to OAuth2Client)
    # ------------------------------------------------------------------

    def generate_auth_url(self, state: Optional[str] = None) -> str:
        """Return the URL that the user must visit to grant permissions."""
        return self.oauth.generate_auth_url(state)

    def exchange_code(self, code: str) -> None:
        """Exchange authorization code for tokens."""
        self.oauth.exchange_code(code)

    # ------------------------------------------------------------------
    # User info helpers
    # ------------------------------------------------------------------

    def _get_user_id_and_country(self) -> tuple[str, str]:
        """Fetch user ID and country from Tidal API."""
        url = f"{TIDAL_API_BASE}/users/me"
        resp = self._get(url, {})
        return resp["data"]["id"], resp["data"]["attributes"]["country"]

    def _ensure_user_id_and_country(self) -> tuple[str, str]:
        """Ensure user ID and country are loaded."""
        if self.oauth.user_id is None or self.oauth.user_country is None:
            self.oauth.user_id, self.oauth.user_country = self._get_user_id_and_country()
            # Save to cache
            self.oauth._save_cached_tokens()
        return self.oauth.user_id, self.oauth.user_country

    # ------------------------------------------------------------------
    # HTTP helpers with rate limiting
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        """Get headers for authenticated requests."""
        return {"Authorization": f"Bearer {self.oauth.access_token}"}

    def _calculate_retry_delay(self, response: requests.Response, retry_count: int, base_delay: float) -> float:
        """Calculate retry delay for rate limiting."""
        # Check for Retry-After header
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

        # Exponential backoff with jitter
        exponential_delay = base_delay * (2 ** (retry_count - 1))
        capped_delay = min(exponential_delay, 60)
        jitter = random.uniform(0.75, 1.25)
        return capped_delay * jitter

    def _get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 5,
        **kwargs,
    ) -> Dict[str, Any]:
        """GET request with rate limiting retry logic."""
        self.oauth.ensure_valid_token()

        if params is None:
            full_url = url
        else:
            full_url = f"{url}?{urlencode(params, doseq=True)}"

        retry_count = 0
        base_delay = 1

        while retry_count <= max_retries:
            try:
                resp = requests.get(full_url, headers=self._headers(), timeout=15, **kwargs)

                # Handle rate limiting
                if resp.status_code == 429:
                    if retry_count >= max_retries:
                        raise RuntimeError(f"Rate limit exceeded after {max_retries} retries for URL: {url}")

                    retry_count += 1
                    wait_time = self._calculate_retry_delay(resp, retry_count, base_delay)
                    time.sleep(wait_time)
                    continue

                resp.raise_for_status()
                return resp.json()

            except requests.RequestException as e:
                raise requests.HTTPError(f"Request failed for URL {url}: {e}")

        raise RuntimeError(f"Unexpected error: exceeded retry logic for URL: {url}")

    def _post(self, url: str, max_retries: int = 3, **kwargs) -> Dict[str, Any]:
        """POST request with rate limiting retry logic."""
        self.oauth.ensure_valid_token()

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
                logger.warning(f"POST rate limited (429) - retry {retry_count}/{max_retries} " f"after {wait_time:.1f}s")
                time.sleep(wait_time)
                continue

            # Handle other errors
            if resp.status_code >= 400:
                raise RuntimeError(f"TIDAL POST {url} failed with status {resp.status_code}: {resp.text}")

            return resp.json()

        raise RuntimeError(f"Unexpected error: exceeded retry logic for POST {url}")

    # ------------------------------------------------------------------
    # Data conversion helpers
    # ------------------------------------------------------------------

    def _get_song_id(self, isrc: str) -> str:
        """Get the Tidal track ID for a given ISRC."""
        self._ensure_user_id_and_country()

        search_url = f"{TIDAL_API_BASE}/tracks"
        params = {
            "include": ["artists", "albums"],
            "filter[isrc]": isrc,
            "countryCode": self.oauth.user_country,
        }

        try:
            resp = self._get(search_url, params=params)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                raise TidalSongNotInRegionError(f"Track with ISRC {isrc} not found in your region.")
            raise RuntimeError(f"Failed to search for track with ISRC {isrc}: {e}")

        return resp["data"][0]["id"]

    def _song_from_api(self, id: str) -> Song:
        """Convert Tidal API track to Song object."""
        try:
            resp = self._get(
                f"{TIDAL_API_BASE}/tracks/{id}",
                {"include": ["artists", "albums"], "countryCode": self.oauth.user_country},
            )
            data = resp["data"]
            items = resp["included"]

            artists = []
            album = None

            for item in items:
                if item["type"] == "albums":
                    album = item["attributes"]["title"]
                elif item["type"] == "artists":
                    artists.append(item["attributes"]["name"])

            return Song(
                isrc=data["attributes"]["isrc"],
                title=data["attributes"]["title"],
                artist=artists,
                album=album,
                duration_ms=data["attributes"]["duration"],
            )
        except TidalSongNotInRegionError:
            raise
        except Exception as e:
            raise Exception(f"Failed to parse track {id}: {e}")

    def _get_tracks_from_url(self, url: str, max_pages: int = 1000, max_tracks: int = 50000) -> List[Song]:
        """Get tracks from a paginated URL with safety limits."""
        tracks: List[Song] = []
        current_url = url
        page_count = 0
        consecutive_failures = 0
        max_consecutive_failures = 3

        try:
            while current_url:
                page_count += 1

                # Hard limit check
                if len(tracks) >= max_tracks:
                    logger.warning(f"Reached maximum track limit ({max_tracks}), stopping pagination")
                    break

                # Soft warning for high page count
                if page_count > max_pages:
                    logger.warning(
                        f"Exceeded expected page limit ({max_pages}), " f"continuing due to next_url (page {page_count})"
                    )

                try:
                    resp = self._get(url=current_url, max_retries=10)
                    consecutive_failures = 0
                except requests.RequestException as e:
                    consecutive_failures += 1
                    logger.error(
                        f"Failed to fetch page {page_count} " f"(failure {consecutive_failures}/{max_consecutive_failures}): {e}"
                    )
                    raise RuntimeError(f"Too many consecutive API failures ({consecutive_failures}), " "stopping pagination")

                data = resp.get("data", [])
                links = resp.get("links", {})

                # Handle empty playlist on first page
                if page_count == 1 and not data:
                    raise TidalEmptyPlaylistError("Tidal playlist is empty.")

                # Process tracks on this page
                for item in data:
                    if item.get("type") == "tracks":
                        try:
                            song = self._song_from_api(item["id"])
                            tracks.append(song)
                        except TidalSongNotInRegionError:
                            logger.warning(f"Song {item['id']} not available in region, skipping")
                        except Exception as e:
                            logger.warning(f"Failed to process song {item.get('id', 'unknown')}: {e}")

                # Check for next page
                next_url = links.get("next")
                if next_url:
                    if next_url.startswith("/"):
                        current_url = f"{TIDAL_API_BASE}{next_url}"
                    else:
                        current_url = next_url
                else:
                    break

            if len(tracks) >= max_tracks and current_url:
                logger.warning(f"Stopped at track limit ({max_tracks}) with more pages available")
            elif page_count > max_pages:
                logger.info(f"Completed pagination with {page_count} pages " f"(exceeded soft limit of {max_pages})")

        except TidalEmptyPlaylistError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error while fetching tracks: {e}")
            raise RuntimeError(f"Failed to fetch tracks after {page_count} pages: {e}")

        logger.info(f"Successfully fetched {len(tracks)} tracks across {page_count} pages")
        return tracks

    def _playlist_from_api(self, id: str) -> Playlist:
        """Convert Tidal API playlist to Playlist object."""
        url = f"{TIDAL_API_BASE}/playlists/{id}"
        resp = self._get(url, {"countryCode": self.oauth.user_country})
        data = resp["data"]

        try:
            songs = self._get_tracks_from_url(f"{TIDAL_API_BASE}/playlists/{id}/relationships/items")
        except TidalEmptyPlaylistError:
            songs = []
            logger.warning(f"Empty playlist {id}")

        # Get cover image
        cover_image_list = data["attributes"].get("imageLinks", {})
        if cover_image_list:
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

    # ------------------------------------------------------------------
    # Playlist operations
    # ------------------------------------------------------------------

    def _create_playlist(self, playlist: Playlist) -> str:
        """Create a new playlist on Tidal."""
        self._ensure_user_id_and_country()

        create_playlists_url = f"{TIDAL_API_BASE}/playlists"
        params = {
            "data": {
                "type": "playlists",
                "attributes": {
                    "name": playlist.name,
                    "description": playlist.description,
                    "privacy": "PRIVATE",
                },
            }
        }

        resp = self._post(create_playlists_url, params=params)
        return resp["data"]["id"]

    def _add_songs_to_playlist(self, playlist_id: str, songs: List[Song]) -> None:
        """Add songs to a Tidal playlist."""
        self._ensure_user_id_and_country()

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

            songs_list.append({"type": "tracks", "id": song_id})

        params = {"data": songs_list, "meta": {"positionBefore": playlist_id}}

        self._post(add_songs_url, json=params)
        logger.info(f"Added {len(songs)} songs to playlist {playlist_id}.")

    # ------------------------------------------------------------------
    # Public API (BaseClient implementation)
    # ------------------------------------------------------------------

    def authenticate(self) -> Optional[str]:
        """
        Smart authentication helper.

        Returns:
            None if already authenticated, or auth URL string if user needs to authorize
        """
        if self.oauth.is_authenticated():
            try:
                self.oauth.ensure_valid_token()
                return None
            except OAuth2Error:
                pass

        logger.warning("No valid access token found. Generating new auth URL...")
        return self.generate_auth_url()

    def get_all_playlists(self) -> List[Playlist]:
        """Get all playlists for the authenticated user."""
        self.oauth.ensure_valid_token()
        self._ensure_user_id_and_country()

        playlists: List[Playlist] = []
        url = f"{TIDAL_API_BASE}/playlists"

        resp = self._get(
            url,
            {
                "filter[r.owners.id]": self.oauth.user_id,
                "countryCode": self.oauth.user_country,
            },
        )

        for data in tqdm(resp["data"]):
            logger.debug(f"Found playlist: {data['attributes']['name']} (ID: {data['id']})")
            playlists.append(self._playlist_from_api(data["id"]))

        return playlists

    def sync_playlists(self, playlist: Playlist) -> None:
        """Create a playlist on Tidal with songs."""
        self.oauth.ensure_valid_token()
        self._ensure_user_id_and_country()

        if not playlist.songs:
            raise TidalEmptyPlaylistError("Cannot sync an empty playlist.")

        # Create the playlist
        playlist_id = self._create_playlist(playlist)

        # Add songs to the playlist
        self._add_songs_to_playlist(playlist_id, playlist.songs)
        logger.info(f"Synced playlist {playlist.name} with ID {playlist_id} on Tidal.")

    def generate_playlist(self, seed: list) -> Playlist:
        """
        Generate a playlist based on seed data.

        Note: Tidal's recommendation API may have different capabilities.
        This is a placeholder implementation.
        """
        raise NotImplementedError("Tidal playlist generation not yet implemented")
