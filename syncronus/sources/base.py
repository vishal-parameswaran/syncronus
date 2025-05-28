from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import asdict, dataclass, field


class BaseClient(ABC):
    """
    Base class for API clients.
    """

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    @abstractmethod
    def authenticate(self):
        """
        Authenticate the client.
        """
        raise NotImplementedError("Subclasses should implement this method.")
        pass

    @abstractmethod
    def get_all_playlists(self) -> List[Dict[str, Any]]:
        """
        Get all playlists for the current user.
        """
        raise NotImplementedError("Subclasses should implement this method.")
        pass

    @abstractmethod
    def sync_playlists(self, playlists: List[Playlist]) -> None:
        """
        Sync playlists with the platform.
        """
        raise NotImplementedError("Subclasses should implement this method.")
        pass

    def generate_playlist(self, seed: list) -> Dict[str, Any]:
        """
        Generate a playlist.
        """
        raise NotImplementedError("Subclasses should implement this method.")
        pass


@dataclass(slots=True)
class Song:
    """A single track on a music platform."""

    isrc: str  # Platform‑specific unique identifier
    title: str
    artist: list[str]
    album: Optional[str] = None
    duration_ms: Optional[int] = None  # Track length in milliseconds
    # The streaming service this song belongs to (e.g. "spotify", "apple").

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Playlist:
    """A collection of songs on a platform."""

    id: str
    name: str
    description: str = ""
    songs: List[Song] = field(default_factory=list)
    service: Optional[str] = None  # Service where the playlist resides
    cover_image_path: Optional[str] = None
    url: Optional[str] = None

    def add_song(self, song: Song) -> None:
        self.songs.append(song)

    def extend(self, songs: List[Song]) -> None:
        self.songs.extend(songs)

    def to_dict(self) -> dict:
        data = asdict(self)
        # Convert nested Song dataclasses to dicts for clean JSON serialisation
        data["songs"] = [s.to_dict() for s in self.songs]
        return data
