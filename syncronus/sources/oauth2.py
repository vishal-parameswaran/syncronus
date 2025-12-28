"""OAuth2 authentication helpers for music service clients."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests


class OAuth2Error(RuntimeError):
    """Raised when OAuth2 authentication/authorization fails."""


class OAuth2Client(ABC):
    """
    Abstract base class for OAuth2 authentication flow.

    Supports both standard OAuth2 Authorization Code flow and PKCE (Proof Key for Code Exchange).
    Handles token caching, automatic refresh, and provides a consistent interface for all services.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scope: list[str],
        cache_path: Path,
        use_pkce: bool = False,
    ) -> None:
        """
        Initialize OAuth2 client.

        Args:
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret
            redirect_uri: Redirect URI for OAuth2 callback
            scope: List of permission scopes to request
            cache_path: Path to cache token data
            use_pkce: Whether to use PKCE extension (more secure for public clients)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.cache_path = Path(cache_path)
        self.use_pkce = use_pkce

        # Token state
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: float = 0.0

        # PKCE state (if enabled)
        self.code_verifier: Optional[str] = None

        # Ensure cache directory exists
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Load cached tokens if available
        self._load_cached_tokens()

    # ------------------------------------------------------------------
    # Abstract methods - must be implemented by subclasses
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def auth_url(self) -> str:
        """Return the OAuth2 authorization endpoint URL."""
        pass

    @property
    @abstractmethod
    def token_url(self) -> str:
        """Return the OAuth2 token endpoint URL."""
        pass

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Return the name of the service (e.g., 'Spotify', 'Tidal')."""
        pass

    # ------------------------------------------------------------------
    # PKCE helpers (optional, only used if use_pkce=True)
    # ------------------------------------------------------------------

    def _make_code_verifier(self) -> str:
        """Generate a random code verifier for PKCE (43-128 characters)."""
        return secrets.token_urlsafe(32)  # 32 bytes = 43 base64url chars

    def _make_code_challenge(self, verifier: str) -> str:
        """Create S256 code challenge from verifier."""
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    # ------------------------------------------------------------------
    # OAuth2 flow methods
    # ------------------------------------------------------------------

    def generate_auth_url(self, state: Optional[str] = None) -> str:
        """
        Generate the authorization URL for the user to visit.

        Args:
            state: Optional state parameter for CSRF protection

        Returns:
            Authorization URL string
        """
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scope),
        }

        if state:
            params["state"] = state

        # Add PKCE parameters if enabled
        if self.use_pkce:
            self.code_verifier = self._make_code_verifier()
            challenge = self._make_code_challenge(self.code_verifier)
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"

            # Cache the verifier for later use
            self._save_verifier()

        return f"{self.auth_url}?{urlencode(params)}"

    def exchange_code(self, code: str) -> None:
        """
        Exchange authorization code for access and refresh tokens.

        Args:
            code: Authorization code from OAuth2 callback

        Raises:
            OAuth2Error: If token exchange fails
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
        }

        # Add client_secret if not using PKCE (or if service requires it)
        if not self.use_pkce or self._requires_client_secret_for_exchange():
            data["client_secret"] = self.client_secret

        # Add PKCE verifier if enabled
        if self.use_pkce:
            if not self.code_verifier:
                # Try to load from cache
                self._load_verifier()
            if self.code_verifier:
                data["code_verifier"] = self.code_verifier

        try:
            resp = requests.post(self.token_url, data=data, timeout=15)
            if resp.status_code != 200:
                raise OAuth2Error(f"{self.service_name} token exchange failed: {resp.text}")
            self._update_tokens(resp.json())
        except requests.RequestException as e:
            raise OAuth2Error(f"Failed to exchange code: {e}")

    def _requires_client_secret_for_exchange(self) -> bool:
        """
        Override this if service requires client_secret even with PKCE.
        Default: False for PKCE flow (more secure).
        """
        return False

    def _refresh_access_token(self) -> None:
        """
        Refresh the access token using the refresh token.

        Raises:
            OAuth2Error: If refresh fails
        """
        if not self.refresh_token:
            raise OAuth2Error(f"No refresh token available for {self.service_name}")

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
        }

        # Some services require client_secret for refresh, others don't
        if self._requires_client_secret_for_refresh():
            data["client_secret"] = self.client_secret

        try:
            resp = requests.post(self.token_url, data=data, timeout=15)
            if resp.status_code != 200:
                raise OAuth2Error(f"{self.service_name} token refresh failed: {resp.text}")
            self._update_tokens(resp.json())
        except requests.RequestException as e:
            raise OAuth2Error(f"Failed to refresh token: {e}")

    def _requires_client_secret_for_refresh(self) -> bool:
        """
        Override this if service requires client_secret for refresh.
        Default: True (most services require it).
        """
        return True

    def ensure_valid_token(self) -> None:
        """
        Ensure a valid access token is available.
        Refreshes automatically if expired.

        Raises:
            OAuth2Error: If no tokens available and user needs to authenticate
        """
        if not self.access_token or time.time() >= self.expires_at:
            if not self.refresh_token:
                raise OAuth2Error(f"No valid token for {self.service_name}. " "Call generate_auth_url() and exchange_code() first.")
            self._refresh_access_token()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _update_tokens(self, payload: Dict[str, Any]) -> None:
        """Update tokens from OAuth2 response and save to cache."""
        self.access_token = payload["access_token"]
        self.expires_at = time.time() + payload["expires_in"] - 60  # 60s margin

        # Update refresh token if provided (not all responses include it)
        if "refresh_token" in payload and payload["refresh_token"]:
            self.refresh_token = payload["refresh_token"]

        self._save_cached_tokens()

    def _load_cached_tokens(self) -> None:
        """Load tokens from cache file if it exists."""
        if not self.cache_path.exists():
            return

        try:
            data = json.loads(self.cache_path.read_text())
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self.expires_at = data.get("expires_at", 0.0)
        except (json.JSONDecodeError, OSError):
            # Cache corrupted or unreadable, ignore
            pass

    def _save_cached_tokens(self) -> None:
        """Save tokens to cache file."""
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }
        self.cache_path.write_text(json.dumps(data))

    def _load_verifier(self) -> None:
        """Load PKCE verifier from cache (if using PKCE)."""
        if not self.cache_path.exists():
            return

        try:
            data = json.loads(self.cache_path.read_text())
            self.code_verifier = data.get("verifier")
        except (json.JSONDecodeError, OSError):
            pass

    def _save_verifier(self) -> None:
        """Save PKCE verifier to cache (if using PKCE)."""
        if not self.use_pkce or not self.code_verifier:
            return

        # Load existing data
        data = {}
        if self.cache_path.exists():
            try:
                data = json.loads(self.cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        # Update with verifier
        data["verifier"] = self.code_verifier
        self.cache_path.write_text(json.dumps(data))

    # ------------------------------------------------------------------
    # Public convenience method
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        """Check if client has valid authentication (access + refresh tokens)."""
        return bool(self.refresh_token)
