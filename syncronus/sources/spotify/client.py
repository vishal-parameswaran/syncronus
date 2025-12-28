"""Spotify API client implementation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from syncronus.logger import get_logger
from syncronus.sources.base import BaseClient, Song, Playlist
from syncronus.sources.oauth2 import OAuth2Client, OAuth2Error


logger = get_logger(__name__)

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

DEFAULT_CACHE_PATH = Path(".cache/spotify_token.json")


class SpotifyAuthError(OAuth2Error):
    """Raised when Spotify authentication/authorization fails."""


class SpotifySongNotInRegionError(RuntimeError):
    """Raised when a song is not available in the user's region."""


class SpotifyOAuth2Client(OAuth2Client):
    """OAuth2 client specifically for Spotify."""

    @property
    def auth_url(self) -> str:
        return SPOTIFY_AUTH_URL

    @property
    def token_url(self) -> str:
        return SPOTIFY_TOKEN_URL

    @property
    def service_name(self) -> str:
        return "Spotify"

    def _requires_client_secret_for_refresh(self) -> bool:
        """Spotify requires client_secret for token refresh."""
        return True

    def _requires_client_secret_for_exchange(self) -> bool:
        """Spotify requires client_secret for code exchange."""
        return True


class SpotifyClient(BaseClient):
    """Spotify Web API client with OAuth2 authentication and token caching."""

    def __init__(
        self,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = "http://127.0.0.1:8888/callback",
        scope: Optional[List[str]] = None,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
    ) -> None:
        """
        Initialize Spotify client.

        Args:
            client_id: Spotify client ID (or from SPOTIFY_CLIENT_ID env var)
            client_secret: Spotify client secret (or from SPOTIFY_CLIENT_SECRET env var)
            redirect_uri: OAuth2 redirect URI
            scope: List of permission scopes
            cache_path: Path to cache tokens
        """
        load_dotenv()  # Load environment variables

        client_id = client_id or os.getenv("SPOTIFY_CLIENT_ID")
        client_secret = client_secret or os.getenv("SPOTIFY_CLIENT_SECRET")

        if not (client_id and client_secret):
            raise SpotifyAuthError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set")

        # Initialize OAuth2 client
        self.oauth = SpotifyOAuth2Client(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope
            or [
                "playlist-read-private",
                "playlist-modify-public",
                "playlist-modify-private",
            ],
            cache_path=Path(cache_path),
            use_pkce=False,  # Spotify doesn't require PKCE
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
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        """Get headers for authenticated requests."""
        return {"Authorization": f"Bearer {self.oauth.access_token}"}

    def _get(self, url: str, **kwargs) -> Dict[str, Any]:
        """GET request with authentication."""
        self.oauth.ensure_valid_token()
        resp = requests.get(url, headers=self._headers(), timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, url: str, **kwargs) -> Dict[str, Any]:
        """POST request with authentication."""
        self.oauth.ensure_valid_token()
        resp = requests.post(url, headers=self._headers(), timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _put(self, url: str, **kwargs) -> Dict[str, Any]:
        """PUT request with authentication."""
        self.oauth.ensure_valid_token()
        resp = requests.put(url, headers=self._headers(), timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    # ------------------------------------------------------------------
    # Data conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _song_from_api(item: Dict[str, Any]) -> Song:
        """Convert Spotify API track item to Song object."""
        track = item.get("track", item)
        return Song(
            isrc=track["external_ids"]["isrc"],
            title=track["name"],
            artist=[artist["name"] for artist in track["artists"]],
            album=track["album"]["name"],
            duration_ms=track["duration_ms"],
        )

    def _playlist_from_api(self, item: Dict[str, Any]) -> Playlist:
        """Convert Spotify API playlist item to Playlist object."""
        playlist_id = item["id"]
        tracks = self._get_tracks_from_url(item["tracks"]["href"])

        # Get cover image (prefer largest)
        cover_image = None
        if item.get("images"):
            cover_image = item["images"][0]["url"]

        return Playlist(
            id=playlist_id,
            name=item["name"],
            description=item.get("description", ""),
            songs=tracks,
            service="spotify",
            url=item["external_urls"]["spotify"],
            cover_image_path=cover_image,
        )

    def _get_tracks_from_url(self, url: str) -> List[Song]:
        """Get all tracks from a paginated URL."""
        tracks: List[Song] = []
        while url:
            data = self._get(url)
            for item in data["items"]:
                if item and item.get("track"):
                    try:
                        tracks.append(self._song_from_api(item))
                    except (KeyError, TypeError) as e:
                        logger.warning(f"Failed to parse track: {e}")
            url = data.get("next")
        return tracks

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

        playlists: List[Playlist] = []
        url = f"{SPOTIFY_API_BASE}/me/playlists"

        while url:
            data = self._get(url)
            for item in data["items"]:
                try:
                    playlists.append(self._playlist_from_api(item))
                except Exception as e:
                    logger.error(f"Failed to parse playlist {item.get('id')}: {e}")
            url = data.get("next")

        return playlists

    def sync_playlists(self, playlist: Playlist) -> None:
        """Create/update a playlist on Spotify."""
        self.oauth.ensure_valid_token()

        if not playlist.songs:
            logger.warning(f"Playlist '{playlist.name}' has no songs to sync")
            return

        # Create playlist
        created = self.create_playlist(
            name=playlist.name,
            description=playlist.description,
            public=False,
            songs=playlist.songs,
        )
        logger.info(f"Synced playlist '{playlist.name}' with ID {created.id}")

    def create_playlist(
        self,
        name: str,
        *,
        description: str = "",
        public: bool = False,
        songs: Optional[List[Song]] = None,
    ) -> Playlist:
        """Create a new playlist on Spotify."""
        self.oauth.ensure_valid_token()

        # Get user ID
        user_data = self._get(f"{SPOTIFY_API_BASE}/me")
        user_id = user_data["id"]

        # Create playlist
        create_url = f"{SPOTIFY_API_BASE}/users/{user_id}/playlists"
        payload = {
            "name": name,
            "description": description,
            "public": public,
        }
        playlist_data = self._post(create_url, json=payload)

        # Add songs if provided
        if songs:
            # Convert Song objects to Spotify URIs via ISRC search
            uris = []
            for song in songs:
                try:
                    search_url = f"{SPOTIFY_API_BASE}/search"
                    params = {"q": f"isrc:{song.isrc}", "type": "track", "limit": 1}
                    result = self._get(search_url, params=params)

                    if result["tracks"]["items"]:
                        uris.append(result["tracks"]["items"][0]["uri"])
                except Exception as e:
                    logger.warning(f"Failed to find song {song.isrc}: {e}")

            # Add tracks to playlist (in batches of 100)
            playlist_id = playlist_data["id"]
            add_url = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks"

            for i in range(0, len(uris), 100):
                batch = uris[i : i + 100]
                self._post(add_url, json={"uris": batch})

        return self._playlist_from_api(playlist_data)

    def generate_playlist(
        self,
        name: str,
        genres: List[str],
        *,
        description: str = "Generated by Syncronus",
        total_songs: int = 25,
        public: bool = False,
    ) -> Playlist:
        """
        Generate a playlist based on genres using Spotify recommendations.

        Args:
            name: Playlist name
            genres: List of seed genres
            description: Playlist description
            total_songs: Number of songs to generate
            public: Whether playlist is public

        Returns:
            Created Playlist object
        """
        self.oauth.ensure_valid_token()

        # Get recommendations
        recommendations_url = f"{SPOTIFY_API_BASE}/recommendations"
        params = {
            "seed_genres": ",".join(genres[:5]),  # Spotify allows max 5 seeds
            "limit": min(total_songs, 100),
        }
        data = self._get(recommendations_url, params=params)

        # Convert tracks to Song objects
        songs = [self._song_from_api(track) for track in data["tracks"]]

        # Create the playlist
        return self.create_playlist(
            name=name,
            description=description,
            public=public,
            songs=songs,
        )
