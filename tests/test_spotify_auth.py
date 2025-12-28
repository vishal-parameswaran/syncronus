import json
import requests
from syncronus.sources.spotify import SpotifyClient
from tests.utils import FakeResponse

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"


def test_spotify_generate_auth_url(tmp_cache):
    client = SpotifyClient(
        client_id="cid",
        client_secret="secret",
        cache_path=tmp_cache,
    )
    url = client.generate_auth_url(state="xyz")
    assert url.startswith(SPOTIFY_AUTH_URL)
    assert "client_id=cid" in url
    assert "response_type=code" in url
    assert "redirect_uri=" in url


def test_spotify_exchange_code_updates_tokens(monkeypatch, tmp_cache, tokens_payload):
    def fake_post(url, data=None, timeout=15, **kwargs):
        assert url == SPOTIFY_TOKEN_URL
        assert data["grant_type"] == "authorization_code"
        assert data["client_id"] == "cid"
        assert data["client_secret"] == "secret"
        return FakeResponse(200, json_data=tokens_payload)

    monkeypatch.setattr(requests, "post", fake_post)

    client = SpotifyClient(
        client_id="cid",
        client_secret="secret",
        cache_path=tmp_cache,
    )
    client.exchange_code("code123")

    # Tokens populated and cached
    assert client.oauth.access_token == "access123"
    assert client.oauth.refresh_token == "refresh123"
    data = json.loads(tmp_cache.read_text())
    assert data["access_token"] == "access123"
    assert data["refresh_token"] == "refresh123"


def test_spotify_authenticate_returns_none_when_valid(tmp_cache):
    client = SpotifyClient(
        client_id="cid",
        client_secret="secret",
        cache_path=tmp_cache,
    )
    # Seed valid tokens
    client.oauth.access_token = "access123"
    client.oauth.refresh_token = "refresh123"
    client.oauth.expires_at = 9e12  # far in the future

    assert client.authenticate() is None


def test_spotify_authenticate_returns_url_when_no_tokens(tmp_cache):
    client = SpotifyClient(
        client_id="cid",
        client_secret="secret",
        cache_path=tmp_cache,
    )
    # Clear tokens
    client.oauth.access_token = None
    client.oauth.refresh_token = None
    # Returns URL when not authenticated
    url = client.authenticate()
    assert isinstance(url, str)
    assert url.startswith(SPOTIFY_AUTH_URL)
