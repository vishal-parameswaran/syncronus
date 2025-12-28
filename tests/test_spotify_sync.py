import requests
from syncronus.sources import Song, Playlist
from syncronus.sources.spotify import SpotifyClient
from tests.utils import FakeResponse

SPOTIFY_API_BASE = "https://api.spotify.com/v1"


def test_spotify_create_playlist_with_songs(monkeypatch, tmp_cache):
    """Test creating a Spotify playlist and adding songs."""
    responses = {
        "user_created": False,
        "playlist_created": False,
        "songs_added": False,
    }

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        # Get user ID
        if url == f"{SPOTIFY_API_BASE}/me":
            return FakeResponse(200, json_data={"id": "user123"})
        # Search for song by ISRC
        elif "/search" in url and params:
            responses["user_created"] = True
            isrc = params["q"].replace("isrc:", "")
            return FakeResponse(200, json_data={"tracks": {"items": [{"uri": f"spotify:track:{isrc}"}]}})
        # Get tracks from playlist (empty initially after creation)
        elif "/playlists/" in url and "/tracks" in url:
            return FakeResponse(200, json_data={"items": [], "next": None})
        raise ValueError(f"Unexpected GET: {url}")

    def fake_post(url, headers=None, timeout=15, json=None, **kwargs):
        # Create playlist
        if "playlists" in url and "/tracks" not in url:
            responses["playlist_created"] = True
            return FakeResponse(
                200,
                json_data={
                    "id": "newplaylist",
                    "name": json["name"],
                    "description": json["description"],
                    "external_urls": {"spotify": "https://open.spotify.com/playlist/newplaylist"},
                    "images": [],
                    "tracks": {"href": f"{SPOTIFY_API_BASE}/playlists/newplaylist/tracks"},
                },
            )
        # Add tracks
        elif "/tracks" in url:
            responses["songs_added"] = True
            assert "uris" in json
            return FakeResponse(200, json_data={"snapshot_id": "snap123"})
        raise ValueError(f"Unexpected POST: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    client = SpotifyClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12

    songs = [
        Song(isrc="ISRC001", title="Song 1", artist=["A1"], album="Alb1", duration_ms=180000),
        Song(isrc="ISRC002", title="Song 2", artist=["A2"], album="Alb2", duration_ms=200000),
    ]

    playlist = client.create_playlist(
        name="Test Playlist",
        description="Created by test",
        public=False,
        songs=songs,
    )

    assert responses["user_created"]
    assert responses["playlist_created"]
    assert responses["songs_added"]
    assert playlist.id == "newplaylist"
    assert playlist.name == "Test Playlist"


def test_spotify_sync_playlists(monkeypatch, tmp_cache):
    """Test syncing a playlist to Spotify."""

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        if url == f"{SPOTIFY_API_BASE}/me":
            return FakeResponse(200, json_data={"id": "user123"})
        elif "/search" in url:
            isrc = params["q"].replace("isrc:", "")
            return FakeResponse(200, json_data={"tracks": {"items": [{"uri": f"spotify:track:{isrc}"}]}})
        elif "/playlists/" in url and "/tracks" in url:
            return FakeResponse(200, json_data={"items": [], "next": None})
        raise ValueError(f"Unexpected GET: {url}")

    def fake_post(url, headers=None, timeout=15, json=None, **kwargs):
        if "playlists" in url and "/tracks" not in url:
            return FakeResponse(
                200,
                json_data={
                    "id": "synced_pl",
                    "name": json["name"],
                    "description": json["description"],
                    "external_urls": {"spotify": "https://open.spotify.com/playlist/synced_pl"},
                    "images": [],
                    "tracks": {"href": f"{SPOTIFY_API_BASE}/playlists/synced_pl/tracks"},
                },
            )
        elif "/tracks" in url:
            return FakeResponse(200, json_data={"snapshot_id": "snap456"})
        raise ValueError(f"Unexpected POST: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    client = SpotifyClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12

    playlist = Playlist(
        id="source_pl",
        name="Synced Playlist",
        description="From another service",
        songs=[
            Song(isrc="ISRC101", title="S1", artist=["A"], album="Alb", duration_ms=180000),
        ],
        service="tidal",
    )

    client.sync_playlists(playlist)
    # No exception means success
