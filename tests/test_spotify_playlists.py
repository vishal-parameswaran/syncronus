import requests
from syncronus.sources import Song, Playlist
from syncronus.sources.spotify import SpotifyClient
from tests.utils import FakeResponse

SPOTIFY_API_BASE = "https://api.spotify.com/v1"


def test_spotify_parse_single_song():
    """Test parsing a Spotify track API response into Song object."""
    api_item = {
        "track": {
            "name": "Test Song",
            "external_ids": {"isrc": "USRC12345678"},
            "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
            "album": {"name": "Test Album"},
            "duration_ms": 240000,
        }
    }
    song = SpotifyClient._song_from_api(api_item)
    assert song.title == "Test Song"
    assert song.isrc == "USRC12345678"
    assert song.artist == ["Artist One", "Artist Two"]
    assert song.album == "Test Album"
    assert song.duration_ms == 240000


def test_spotify_get_tracks_from_url_paginated(monkeypatch, tmp_cache):
    """Test fetching tracks across multiple pages."""
    page_1_url = f"{SPOTIFY_API_BASE}/playlists/abc/tracks"
    page_2_url = f"{SPOTIFY_API_BASE}/playlists/abc/tracks?offset=2"

    def fake_get(url, headers=None, timeout=15, **kwargs):
        if url == page_1_url:
            return FakeResponse(
                200,
                json_data={
                    "items": [
                        {
                            "track": {
                                "name": "Song 1",
                                "external_ids": {"isrc": "ISRC1"},
                                "artists": [{"name": "A1"}],
                                "album": {"name": "Alb1"},
                                "duration_ms": 180000,
                            }
                        },
                        {
                            "track": {
                                "name": "Song 2",
                                "external_ids": {"isrc": "ISRC2"},
                                "artists": [{"name": "A2"}],
                                "album": {"name": "Alb2"},
                                "duration_ms": 200000,
                            }
                        },
                    ],
                    "next": page_2_url,
                },
            )
        elif url == page_2_url:
            return FakeResponse(
                200,
                json_data={
                    "items": [
                        {
                            "track": {
                                "name": "Song 3",
                                "external_ids": {"isrc": "ISRC3"},
                                "artists": [{"name": "A3"}],
                                "album": {"name": "Alb3"},
                                "duration_ms": 220000,
                            }
                        },
                    ],
                    "next": None,
                },
            )
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    client = SpotifyClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12

    tracks = client._get_tracks_from_url(page_1_url)
    assert len(tracks) == 3
    assert tracks[0].title == "Song 1"
    assert tracks[1].title == "Song 2"
    assert tracks[2].title == "Song 3"


def test_spotify_playlist_from_api(monkeypatch, tmp_cache):
    """Test converting Spotify playlist API response to Playlist object."""

    def fake_get(url, headers=None, timeout=15, **kwargs):
        if "playlists/abc/tracks" in url:
            return FakeResponse(
                200,
                json_data={
                    "items": [
                        {
                            "track": {
                                "name": "Track 1",
                                "external_ids": {"isrc": "ISRC001"},
                                "artists": [{"name": "Artist"}],
                                "album": {"name": "Album"},
                                "duration_ms": 180000,
                            }
                        },
                    ],
                    "next": None,
                },
            )
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    client = SpotifyClient(client_id="cid", client_secret="secret", cache_path=tmp_cache)
    client.oauth.access_token = "access123"
    client.oauth.expires_at = 9e12

    playlist_data = {
        "id": "playlist123",
        "name": "My Playlist",
        "description": "Test playlist",
        "tracks": {"href": f"{SPOTIFY_API_BASE}/playlists/abc/tracks"},
        "external_urls": {"spotify": "https://open.spotify.com/playlist/playlist123"},
        "images": [{"url": "https://example.com/image.jpg"}],
    }

    playlist = client._playlist_from_api(playlist_data)
    assert playlist.id == "playlist123"
    assert playlist.name == "My Playlist"
    assert playlist.description == "Test playlist"
    assert len(playlist.songs) == 1
    assert playlist.songs[0].title == "Track 1"
    assert playlist.service == "spotify"
    assert playlist.url == "https://open.spotify.com/playlist/playlist123"
    assert playlist.cover_image_path == "https://example.com/image.jpg"
