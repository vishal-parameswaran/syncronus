import requests
from syncronus.sources import Song, Playlist
from syncronus.sources.tidal import TidalClient, TidalEmptyPlaylistError
from tests.utils import FakeResponse
import pytest

TIDAL_API_BASE = "https://openapi.tidal.com/v2"


def test_tidal_song_from_api(monkeypatch, tmp_cache):
    """Test parsing a Tidal track API response into Song object."""

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        if f"{TIDAL_API_BASE}/tracks/track123" in url:
            return FakeResponse(
                200,
                json_data={
                    "data": {
                        "id": "track123",
                        "attributes": {
                            "title": "Tidal Song",
                            "isrc": "GBUM71234567",
                            "duration": 210000,
                        },
                    },
                    "included": [
                        {"type": "albums", "attributes": {"title": "Album Name"}},
                        {"type": "artists", "attributes": {"name": "Artist One"}},
                        {"type": "artists", "attributes": {"name": "Artist Two"}},
                    ],
                },
            )
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    client = TidalClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12
    client.oauth.user_country = "US"

    song = client._song_from_api("track123")
    assert song.title == "Tidal Song"
    assert song.isrc == "GBUM71234567"
    assert song.artist == ["Artist One", "Artist Two"]
    assert song.album == "Album Name"
    assert song.duration_ms == 210000


def test_tidal_get_tracks_from_url_paginated(monkeypatch, tmp_cache):
    """Test fetching Tidal tracks across multiple pages."""
    page_1_url = f"{TIDAL_API_BASE}/playlists/abc/relationships/items"
    page_2_url = f"{TIDAL_API_BASE}/playlists/abc/relationships/items?page=2"

    call_count = {"track_detail": 0}

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        # Page 1
        if url == page_1_url:
            return FakeResponse(
                200,
                json_data={
                    "data": [
                        {"type": "tracks", "id": "t1"},
                        {"type": "tracks", "id": "t2"},
                    ],
                    "links": {"next": page_2_url},
                },
            )
        # Page 2
        elif url == page_2_url:
            return FakeResponse(
                200,
                json_data={
                    "data": [
                        {"type": "tracks", "id": "t3"},
                    ],
                    "links": {},
                },
            )
        # Track detail calls
        elif "/tracks/t1" in url:
            call_count["track_detail"] += 1
            return FakeResponse(
                200,
                json_data={
                    "data": {"attributes": {"title": "Track 1", "isrc": "ISRC1", "duration": 180000}},
                    "included": [
                        {"type": "albums", "attributes": {"title": "Alb1"}},
                        {"type": "artists", "attributes": {"name": "A1"}},
                    ],
                },
            )
        elif "/tracks/t2" in url:
            call_count["track_detail"] += 1
            return FakeResponse(
                200,
                json_data={
                    "data": {"attributes": {"title": "Track 2", "isrc": "ISRC2", "duration": 200000}},
                    "included": [
                        {"type": "albums", "attributes": {"title": "Alb2"}},
                        {"type": "artists", "attributes": {"name": "A2"}},
                    ],
                },
            )
        elif "/tracks/t3" in url:
            call_count["track_detail"] += 1
            return FakeResponse(
                200,
                json_data={
                    "data": {"attributes": {"title": "Track 3", "isrc": "ISRC3", "duration": 220000}},
                    "included": [
                        {"type": "albums", "attributes": {"title": "Alb3"}},
                        {"type": "artists", "attributes": {"name": "A3"}},
                    ],
                },
            )
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    client = TidalClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12
    client.oauth.user_country = "US"

    tracks = client._get_tracks_from_url(page_1_url)
    assert len(tracks) == 3
    assert tracks[0].title == "Track 1"
    assert tracks[1].title == "Track 2"
    assert tracks[2].title == "Track 3"
    assert call_count["track_detail"] == 3


def test_tidal_empty_playlist_raises_error(monkeypatch, tmp_cache):
    """Test that empty playlist raises TidalEmptyPlaylistError."""

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        if "relationships/items" in url:
            return FakeResponse(200, json_data={"data": [], "links": {}})
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    client = TidalClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12
    client.oauth.user_country = "US"

    with pytest.raises(TidalEmptyPlaylistError):
        client._get_tracks_from_url(f"{TIDAL_API_BASE}/playlists/abc/relationships/items")


def test_tidal_playlist_from_api(monkeypatch, tmp_cache):
    """Test converting Tidal playlist API response to Playlist object."""

    def fake_get(url, headers=None, timeout=15, params=None, **kwargs):
        if f"{TIDAL_API_BASE}/playlists/pl123" in url and "relationships" not in url:
            return FakeResponse(
                200,
                json_data={
                    "data": {
                        "id": "pl123",
                        "attributes": {
                            "name": "My Tidal Playlist",
                            "description": "Test description",
                            "imageLinks": [
                                {"href": "https://example.com/small.jpg", "meta": {"width": 320}},
                                {"href": "https://example.com/large.jpg", "meta": {"width": 1280}},
                            ],
                        },
                    },
                },
            )
        elif "relationships/items" in url:
            return FakeResponse(
                200,
                json_data={
                    "data": [{"type": "tracks", "id": "track1"}],
                    "links": {},
                },
            )
        elif "/tracks/track1" in url:
            return FakeResponse(
                200,
                json_data={
                    "data": {"attributes": {"title": "T1", "isrc": "ISRC1", "duration": 180000}},
                    "included": [
                        {"type": "albums", "attributes": {"title": "Alb"}},
                        {"type": "artists", "attributes": {"name": "Art"}},
                    ],
                },
            )
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    client = TidalClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12
    client.oauth.user_country = "US"

    playlist = client._playlist_from_api("pl123")
    assert playlist.id == "pl123"
    assert playlist.name == "My Tidal Playlist"
    assert playlist.description == "Test description"
    assert len(playlist.songs) == 1
    assert playlist.songs[0].title == "T1"
    assert playlist.service == "tidal"
    assert playlist.url == "https://listen.tidal.com/playlist/pl123"
    assert playlist.cover_image_path == "https://example.com/large.jpg"  # largest
