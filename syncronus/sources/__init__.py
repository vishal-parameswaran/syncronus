"""Music service client sources."""

from syncronus.sources.base import BaseClient, Song, Playlist
from syncronus.sources.spotify import SpotifyClient, SpotifyAuthError
from syncronus.sources.tidal import TidalClient, TidalAuthError

__all__ = [
    # Base classes and models
    "BaseClient",
    "Song",
    "Playlist",
    # Spotify
    "SpotifyClient",
    "SpotifyAuthError",
    # Tidal
    "TidalClient",
    "TidalAuthError",
]
