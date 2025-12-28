import requests
from syncronus.sources import Song, Playlist
from syncronus.sources.tidal import TidalClient, TidalEmptyPlaylistError
from tests.utils import FakeResponse
import pytest

TIDAL_API_BASE = "https://openapi.tidal.com/v2"


def test_tidal_create_playlist(monkeypatch, tmp_cache):
    """Test creating a Tidal playlist."""

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        if "/users/me" in url:
            return FakeResponse(200, json_data={"data": {"id": "user456", "attributes": {"country": "US"}}})
        raise ValueError(f"Unexpected GET: {url}")

    def fake_post(url, headers=None, timeout=15, json=None, params=None, **kwargs):
        if "/playlists" in url and "relationships" not in url:
            assert params["data"]["type"] == "playlists"
            assert params["data"]["attributes"]["name"] == "New Tidal Playlist"
            return FakeResponse(200, json_data={"data": {"id": "newpl123"}})
        raise ValueError(f"Unexpected POST: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    client = TidalClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12

    playlist = Playlist(
        id="temp",
        name="New Tidal Playlist",
        description="Test",
        songs=[],
    )

    playlist_id = client._create_playlist(playlist)
    assert playlist_id == "newpl123"


def test_tidal_add_songs_to_playlist(monkeypatch, tmp_cache):
    """Test adding songs to a Tidal playlist."""

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        if "/users/me" in url:
            return FakeResponse(200, json_data={"data": {"id": "user456", "attributes": {"country": "US"}}})
        # Handle _get_song_id which uses _get internally with params
        elif "/tracks" in url and not "/relationships" in url:
            # This is the search endpoint
            return FakeResponse(200, json_data={"data": [{"id": f"track_found"}]})
        raise ValueError(f"Unexpected GET: {url}")

    def fake_post(url, headers=None, timeout=15, json=None, **kwargs):
        if "relationships/items" in url:
            # Should have 2 tracks
            assert len(json["data"]) == 2
            assert json["data"][0]["type"] == "tracks"
            return FakeResponse(201, json_data={})
        raise ValueError(f"Unexpected POST: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    client = TidalClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12

    songs = [
        Song(isrc="ISRC201", title="S1", artist=["A"], album="Alb", duration_ms=180000),
        Song(isrc="ISRC202", title="S2", artist=["B"], album="Alb", duration_ms=200000),
    ]

    client._add_songs_to_playlist("pl456", songs)
    # No exception means success


def test_tidal_sync_playlists_empty_raises_error(monkeypatch, tmp_cache):
    """Test that syncing an empty playlist raises error."""

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        if "/users/me" in url:
            return FakeResponse(200, json_data={"data": {"id": "user456", "attributes": {"country": "US"}}})
        raise ValueError(f"Unexpected GET: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    client = TidalClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12

    empty_playlist = Playlist(
        id="empty",
        name="Empty",
        description="",
        songs=[],
    )

    with pytest.raises(TidalEmptyPlaylistError):
        client.sync_playlists(empty_playlist)


def test_tidal_sync_playlists_full_flow(monkeypatch, tmp_cache):
    """Test full sync flow: create playlist + add songs."""

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        if "/users/me" in url:
            return FakeResponse(200, json_data={"data": {"id": "user789", "attributes": {"country": "UK"}}})
        elif "/tracks" in url and ("filter[isrc]" in url or (params and "filter[isrc]" in params)):
            # Extract ISRC
            if params and "filter[isrc]" in params:
                isrc = params["filter[isrc]"]
            else:
                import re

                match = re.search(r"filter%5Bisrc%5D=([^&]+)", url)
                isrc = match.group(1) if match else "UNKNOWN"
            return FakeResponse(200, json_data={"data": [{"id": f"tid_{isrc}"}]})
        raise ValueError(f"Unexpected GET: {url}")

    def fake_post(url, headers=None, timeout=15, json=None, params=None, **kwargs):
        if "/playlists" in url and "relationships" not in url:
            return FakeResponse(200, json_data={"data": {"id": "syncedpl"}})
        elif "relationships/items" in url:
            return FakeResponse(201, json_data={})
        raise ValueError(f"Unexpected POST: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    client = TidalClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12

    playlist = Playlist(
        id="orig",
        name="Synced from Spotify",
        description="Cross-platform sync",
        songs=[
            Song(isrc="ISRC301", title="Track 1", artist=["A"], album="Alb", duration_ms=180000),
        ],
        service="spotify",
    )

    client.sync_playlists(playlist)
    # Success if no exception
