"""Tidal API client."""

from .client import (
    TidalClient,
    TidalAuthError,
    TidalSongNotInRegionError,
    TidalEmptyPlaylistError,
)

__all__ = [
    "TidalClient",
    "TidalAuthError",
    "TidalSongNotInRegionError",
    "TidalEmptyPlaylistError",
]
