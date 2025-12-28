"""Spotify API client."""

from .client import (
    SpotifyClient,
    SpotifyAuthError,
    SpotifySongNotInRegionError,
)

__all__ = [
    "SpotifyClient",
    "SpotifyAuthError",
    "SpotifySongNotInRegionError",
]
