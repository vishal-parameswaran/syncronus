# Test Suite Documentation

## Overview
Comprehensive pytest-based test suite for the Syncronus music service integration platform.

## Test Coverage: 28 Tests, All Passing ✅

### Test Structure
```
tests/
├── conftest.py              # Shared fixtures (tokens_payload, tmp_cache)
├── utils.py                 # FakeResponse helper for mocking requests
├── test_sources_exports.py  # Package structure validation
├── test_spotify_auth.py     # Spotify OAuth2 flow (5 tests)
├── test_spotify_playlists.py # Spotify playlist parsing (3 tests)
├── test_spotify_sync.py     # Spotify sync operations (2 tests)
├── test_tidal_auth.py       # Tidal OAuth2 + PKCE flow (4 tests)
├── test_tidal_playlists.py  # Tidal playlist parsing (4 tests)
├── test_tidal_sync.py       # Tidal sync operations (4 tests)
└── test_token_refresh.py    # Token lifecycle management (6 tests)
```

## Test Categories

### 1. Authentication Tests (9 tests)
**Spotify OAuth2 (5 tests)**
- ✅ `test_spotify_generate_auth_url` - URL generation with correct params
- ✅ `test_spotify_exchange_code_updates_tokens` - Code exchange flow
- ✅ `test_spotify_authenticate_returns_none_when_valid` - Valid token check
- ✅ `test_spotify_authenticate_returns_url_when_no_tokens` - Auth required flow
- ✅ Test validates standard OAuth2 flow without PKCE

**Tidal OAuth2 + PKCE (4 tests)**
- ✅ `test_tidal_generate_auth_url_includes_pkce` - PKCE parameters in URL
- ✅ `test_tidal_exchange_code_updates_tokens` - Code exchange with verifier
- ✅ `test_tidal_refresh_uses_client_id_only` - No client_secret in refresh
- ✅ `test_tidal_fetches_user_id_and_country` - User metadata retrieval
- ✅ Test validates PKCE S256 implementation

### 2. Playlist Parsing Tests (7 tests)
**Spotify (3 tests)**
- ✅ `test_spotify_parse_single_song` - Song object creation from API data
- ✅ `test_spotify_get_tracks_from_url_paginated` - Multi-page track fetching
- ✅ `test_spotify_playlist_from_api` - Full playlist conversion with metadata

**Tidal (4 tests)**
- ✅ `test_tidal_song_from_api` - Song parsing with included artists/albums
- ✅ `test_tidal_get_tracks_from_url_paginated` - Paginated track retrieval
- ✅ `test_tidal_empty_playlist_raises_error` - Empty playlist handling
- ✅ `test_tidal_playlist_from_api` - Full playlist with image selection (largest)

### 3. Sync Operations Tests (6 tests)
**Spotify (2 tests)**
- ✅ `test_spotify_create_playlist_with_songs` - Create + add songs flow
- ✅ `test_spotify_sync_playlists` - Full sync from external playlist

**Tidal (4 tests)**
- ✅ `test_tidal_create_playlist` - Playlist creation
- ✅ `test_tidal_add_songs_to_playlist` - ISRC search + song addition
- ✅ `test_tidal_sync_playlists_empty_raises_error` - Empty validation
- ✅ `test_tidal_sync_playlists_full_flow` - End-to-end sync

### 4. Token Lifecycle Tests (6 tests)
- ✅ `test_spotify_token_auto_refresh_on_expiry` - Auto-refresh on expiration
- ✅ `test_tidal_token_auto_refresh_on_expiry` - Tidal-specific refresh (no secret)
- ✅ `test_oauth_raises_error_when_no_refresh_token` - Error handling
- ✅ `test_authenticate_method_handles_expired_token_gracefully` - Seamless refresh
- ✅ `test_token_refresh_updates_cache` - Cache persistence
- ✅ `test_token_with_margin_triggers_refresh` - 60-second safety margin

### 5. Package Structure Tests (1 test)
- ✅ `test_sources_exports` - Validates public API exports

## Test Approach

### Mocking Strategy
- **Network isolation**: All HTTP requests mocked using `monkeypatch`
- **FakeResponse**: Custom response object mimicking `requests.Response`
- **Temp caches**: Each test uses isolated token cache via `tmp_path` fixture

### Key Testing Patterns

**1. Pagination Testing**
```python
def fake_get(url, ...):
    if url == page_1_url:
        return {"items": [...], "next": page_2_url}
    elif url == page_2_url:
        return {"items": [...], "next": None}
```

**2. OAuth Flow Validation**
```python
# Exchange code
assert data["grant_type"] == "authorization_code"
assert data["client_id"] == "cid"
# For PKCE
assert "code_verifier" in data
```

**3. State Tracking**
```python
responses = {"playlist_created": False, "songs_added": False}
# Validate both steps occurred
assert responses["playlist_created"]
assert responses["songs_added"]
```

## Coverage Areas

### ✅ Covered
- OAuth2 Authorization Code flow (standard + PKCE)
- Token refresh and caching
- Playlist parsing with pagination
- Song object creation from API responses
- Sync operations (create + populate)
- Error handling (empty playlists, missing tokens)
- ISRC-based cross-platform song matching
- User metadata retrieval (Tidal-specific)

### 🔄 Future Enhancements
- Rate limiting behavior (429 responses)
- Network failure retry logic
- Large playlist handling (>10k songs)
- Concurrent request testing
- Real API integration tests (optional, with credentials)
- Song unavailability scenarios (region restrictions)
- Playlist update/modification operations

## Running Tests

**All tests:**
```bash
pytest tests/
```

**Specific category:**
```bash
pytest tests/test_spotify_auth.py
pytest tests/test_tidal_playlists.py
```

**With coverage:**
```bash
pytest tests/ --cov=syncronus --cov-report=html
```

**Verbose output:**
```bash
pytest tests/ -v
```

**Quick run (quiet mode):**
```bash
pytest tests/ -q
```

## Test Fixtures

### `tmp_cache` (from conftest.py)
- Provides isolated cache path per test
- Automatically cleaned up after test
- Prevents test pollution

### `tokens_payload` (from conftest.py)
- Standard OAuth2 token response structure
- Reusable across auth tests

### `FakeResponse` (from utils.py)
- Mimics `requests.Response` behavior
- Supports JSON responses
- Implements `raise_for_status()`

## Best Practices Demonstrated

1. **Isolation**: Each test is independent with temp caches
2. **Mocking**: No real network calls, fast execution
3. **Clarity**: Descriptive test names indicate what's being tested
4. **Reusability**: Shared fixtures reduce duplication
5. **Error Cases**: Tests both success and failure paths
6. **Real-world Scenarios**: Tests match actual API patterns

## Dependencies

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^7.4.0"
pytest-asyncio = "^0.21.0"
```

Already installed in your environment ✅
