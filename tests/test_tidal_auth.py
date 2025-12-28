import json
import requests
from syncronus.sources.tidal import TidalClient
from tests.utils import FakeResponse

TIDAL_AUTH_URL = "https://login.tidal.com/authorize"
TIDAL_TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
TIDAL_API_BASE = "https://openapi.tidal.com/v2"


def test_tidal_generate_auth_url_includes_pkce(tmp_cache):
    client = TidalClient(
        client_id="cid",
        client_secret="secret",
        cache_path=tmp_cache,
    )
    url = client.generate_auth_url(state="abc")
    assert url.startswith(TIDAL_AUTH_URL)
    assert "client_id=cid" in url
    assert "code_challenge_method=S256" in url
    assert "code_challenge=" in url
    # PKCE verifier cached
    data = json.loads(tmp_cache.read_text())
    assert "verifier" in data


def test_tidal_exchange_code_updates_tokens(monkeypatch, tmp_cache, tokens_payload):
    def fake_post(url, data=None, timeout=15, **kwargs):
        assert url == TIDAL_TOKEN_URL
        assert data["grant_type"] == "authorization_code"
        assert data["client_id"] == "cid"
        # Tidal with PKCE sends code_verifier, not client_secret
        assert "code_verifier" in data
        return FakeResponse(200, json_data=tokens_payload)

    monkeypatch.setattr(requests, "post", fake_post)

    client = TidalClient(
        client_id="cid",
        client_secret="secret",
        cache_path=tmp_cache,
    )
    # Ensure verifier exists as generate_auth_url would do
    _ = client.generate_auth_url()
    client.exchange_code("code123")

    assert client.oauth.access_token == "access123"
    assert client.oauth.refresh_token == "refresh123"
    data = json.loads(tmp_cache.read_text())
    assert data["access_token"] == "access123"
    assert data["refresh_token"] == "refresh123"


def test_tidal_refresh_uses_client_id_only(monkeypatch, tmp_cache, tokens_payload):
    def fake_post(url, data=None, timeout=15, **kwargs):
        assert url == TIDAL_TOKEN_URL
        assert data["grant_type"] == "refresh_token"
        assert data["client_id"] == "cid"
        assert "client_secret" not in data
        return FakeResponse(200, json_data=tokens_payload)

    monkeypatch.setattr(requests, "post", fake_post)

    client = TidalClient(
        client_id="cid",
        client_secret="secret",
        cache_path=tmp_cache,
    )
    client.oauth.refresh_token = "refresh123"
    client.oauth.access_token = None
    client.oauth.expires_at = 0

    # Triggers refresh
    assert client.authenticate() is None
    assert client.oauth.access_token == "access123"


def test_tidal_fetches_user_id_and_country(monkeypatch, tmp_cache):
    # Mock GET for users/me
    def fake_get(url, headers=None, timeout=15, **kwargs):
        assert url.startswith(f"{TIDAL_API_BASE}/users/me")
        return FakeResponse(200, json_data={
            "data": {
                "id": "user-1",
                "attributes": {"country": "US"}
            }
        })

    monkeypatch.setattr(requests, "get", fake_get)

    client = TidalClient(
        client_id="cid",
        client_secret="secret",
        cache_path=tmp_cache,
    )
    client.oauth.access_token = "access123"
    client.oauth.refresh_token = "refresh123"
    client.oauth.expires_at = 9e12

    # Calls ensure user info via get_all_playlists path
    client._ensure_user_id_and_country()
    assert client.oauth.user_id == "user-1"
    assert client.oauth.user_country == "US"
