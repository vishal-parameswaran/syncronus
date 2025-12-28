# Sources Architecture

This directory contains implementations for various music streaming service clients.

## Structure

```
sources/
├── base.py              # Abstract BaseClient class and data models (Song, Playlist)
├── oauth2.py            # OAuth2Client abstract class for authentication
├── spotify/             # Spotify implementation
│   ├── __init__.py
│   └── client.py
├── tidal/               # Tidal implementation
│   ├── __init__.py
│   └── client.py
└── __init__.py          # Public API exports
```

## Adding a New Service

To add support for a new music streaming service (e.g., Apple Music, YouTube Music), follow these steps:

### 1. Create a new folder

```bash
mkdir syncronus/sources/your_service
```

### 2. Implement OAuth2 if applicable

Create a subclass of `OAuth2Client` in your service's `client.py`:

```python
from syncronus.sources.oauth2 import OAuth2Client

class YourServiceOAuth2Client(OAuth2Client):
    @property
    def auth_url(self) -> str:
        return "https://auth.yourservice.com/authorize"
    
    @property
    def token_url(self) -> str:
        return "https://auth.yourservice.com/token"
    
    @property
    def service_name(self) -> str:
        return "YourService"
    
    def _requires_client_secret_for_refresh(self) -> bool:
        # Return True if your service requires client_secret for token refresh
        return True
    
    def _requires_client_secret_for_exchange(self) -> bool:
        # Return True if your service requires client_secret for code exchange
        return True
```

**Key configuration points:**
- Set `use_pkce=True` in the constructor if your service requires PKCE
- Override `_requires_client_secret_for_refresh()` based on your service's requirements
- Override `_requires_client_secret_for_exchange()` based on your service's requirements

### 3. Implement the client

Create a subclass of `BaseClient`:

```python
from syncronus.sources.base import BaseClient, Song, Playlist

class YourServiceClient(BaseClient):
    def __init__(self, *, client_id: str, client_secret: str, **kwargs):
        # Initialize OAuth2 client
        self.oauth = YourServiceOAuth2Client(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=kwargs.get('redirect_uri', 'http://localhost:8080/callback'),
            scope=kwargs.get('scope', ['read', 'write']),
            cache_path=kwargs.get('cache_path', Path('.cache/yourservice_token.json')),
            use_pkce=False,  # Set to True if PKCE is required
        )
    
    def authenticate(self) -> Optional[str]:
        """Returns None if authenticated, or auth URL if user needs to authorize."""
        if self.oauth.is_authenticated():
            try:
                self.oauth.ensure_valid_token()
                return None
            except OAuth2Error:
                pass
        return self.oauth.generate_auth_url()
    
    def get_all_playlists(self) -> List[Playlist]:
        """Fetch all playlists for the authenticated user."""
        self.oauth.ensure_valid_token()
        # Implement your API calls here
        pass
    
    def sync_playlists(self, playlist: Playlist) -> None:
        """Create/update a playlist on the service."""
        self.oauth.ensure_valid_token()
        # Implement your API calls here
        pass
    
    def generate_playlist(self, seed: list) -> Playlist:
        """Generate a playlist based on seed data (optional)."""
        self.oauth.ensure_valid_token()
        # Implement if your service supports recommendations
        pass
```

### 4. Create __init__.py

```python
"""YourService API client."""

from .client import YourServiceClient, YourServiceAuthError

__all__ = ["YourServiceClient", "YourServiceAuthError"]
```

### 5. Update sources/__init__.py

Add your client to the public API:

```python
from syncronus.sources.yourservice import YourServiceClient, YourServiceAuthError

__all__ = [
    # ... existing exports
    "YourServiceClient",
    "YourServiceAuthError",
]
```

## Authentication Flow

All services follow the same OAuth2 flow:

1. **Initialize client**: `client = YourServiceClient(client_id="...", client_secret="...")`
2. **Check authentication**: `auth_url = client.authenticate()`
3. **If auth_url is returned**: User visits URL and authorizes
4. **Exchange code**: `client.exchange_code(code)`
5. **Make API calls**: Tokens are automatically refreshed as needed

## Design Patterns

### OAuth2Client (Abstract)
- Handles token lifecycle (fetch, refresh, cache)
- Supports both standard OAuth2 and PKCE flows
- Automatically manages token expiration

### BaseClient (Abstract)
- Defines the interface all music service clients must implement
- Required methods: `authenticate()`, `get_all_playlists()`, `sync_playlists()`
- Optional method: `generate_playlist()`

### Data Models
- **Song**: Represents a single track (uses ISRC for cross-platform matching)
- **Playlist**: Represents a collection of songs

## Service-Specific Differences

| Feature | Spotify | Tidal | Notes |
|---------|---------|-------|-------|
| PKCE | ❌ No | ✅ Yes | Tidal requires PKCE for security |
| Client secret in refresh | ✅ Yes | ❌ No | Tidal only needs client_id |
| Client secret in exchange | ✅ Yes | ❌ No | PKCE replaces client_secret |
| Rate limiting | Basic | Advanced | Tidal has sophisticated retry logic |
| User context | Not required | Required | Tidal needs user_id and country |

## Best Practices

1. **Environment Variables**: Store credentials in `.env` file
2. **Token Caching**: Use separate cache files per service
3. **Error Handling**: Create service-specific exception classes
4. **Rate Limiting**: Implement retry logic with exponential backoff
5. **Logging**: Use the `syncronus.logger` module
6. **Type Hints**: Always include type annotations
7. **ISRC Matching**: Use ISRC codes for cross-platform song matching

## Example Usage

```python
from syncronus.sources import SpotifyClient, TidalClient

# Initialize clients
spotify = SpotifyClient()
tidal = TidalClient()

# Authenticate
spotify_auth_url = spotify.authenticate()
if spotify_auth_url:
    print(f"Visit: {spotify_auth_url}")
    code = input("Enter code: ")
    spotify.exchange_code(code)

# Get playlists
spotify_playlists = spotify.get_all_playlists()

# Sync to another service
for playlist in spotify_playlists:
    tidal.sync_playlists(playlist)
```

## Testing

Create tests in `tests/yourservice/` directory following the pattern in `tests/spotify/connection_test.py`.
