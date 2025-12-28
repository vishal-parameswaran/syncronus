import os
import json
import types
import pytest

class FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None, headers=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}
        self.text = text or json.dumps(self._json_data)

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}: {self.text}")

@pytest.fixture
def tokens_payload():
    return {
        "access_token": "access123",
        "refresh_token": "refresh123",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

@pytest.fixture
def tmp_cache(tmp_path):
    # Create a temp cache path per test to avoid side effects
    cache = tmp_path / ".cache" / "test_token.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    return cache
