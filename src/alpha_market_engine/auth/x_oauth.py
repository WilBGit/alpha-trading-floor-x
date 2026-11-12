from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

AUTHORIZATION_ENDPOINT = "https://twitter.com/i/oauth2/authorize"
TOKEN_ENDPOINT = "https://api.x.com/2/oauth2/token"
USERS_ME_ENDPOINT = "https://api.x.com/2/users/me"
DEFAULT_SCOPES = frozenset({"tweet.read", "tweet.write", "users.read", "offline.access"})
REFRESH_THRESHOLD_SECONDS = 5 * 60


class XOAuthError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class XOAuthTokenStatus(str, Enum):
    MISSING = "MISSING"
    VALID = "VALID"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"
    REFRESH_REQUIRED = "REFRESH_REQUIRED"
    REFRESH_UNAVAILABLE = "REFRESH_UNAVAILABLE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class XOAuthAuthorizationRequest:
    state: str = field(repr=False)
    code_verifier: str = field(repr=False)
    code_challenge: str
    authorization_url: str
    redirect_uri: str
    scopes: frozenset[str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class XOAuthTokenSet:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    granted_scopes: frozenset[str] = frozenset()
    obtained_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    authorized_user_id: str | None = None
    authorized_username: str | None = None
    identity_verified_at: datetime | None = None

    def __repr__(self) -> str:
        return ("XOAuthTokenSet(access_token=<redacted>, refresh_token=<redacted>, "
                f"token_type={self.token_type!r}, expires_at={self.expires_at!r}, "
                f"granted_scopes={sorted(self.granted_scopes)!r}, obtained_at={self.obtained_at!r}, "
                f"authorized_user_id={self.authorized_user_id!r}, "
                f"authorized_username={self.authorized_username!r})")

    @property
    def status(self) -> XOAuthTokenStatus:
        if not self.access_token:
            return XOAuthTokenStatus.MISSING
        if self.expires_at is None:
            return XOAuthTokenStatus.VALID
        now = datetime.now(UTC)
        expires_at = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            return XOAuthTokenStatus.EXPIRED
        if expires_at <= now + timedelta(seconds=REFRESH_THRESHOLD_SECONDS):
            return XOAuthTokenStatus.EXPIRING
        return XOAuthTokenStatus.VALID

    def has_scopes(self, required_scopes: set[str] | frozenset[str]) -> bool:
        return set(required_scopes).issubset(self.granted_scopes)


@dataclass(frozen=True, slots=True)
class XOAuthAccountIdentity:
    user_id: str
    username: str
    name: str | None
    verified_at: datetime


class XOAuthTokenStore:
    """Owner-readable token file outside the repository; never logs token values."""
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.path.expanduser("~/.hermes/state/alpha_trading_floor_x_oauth.json"))

    def load(self) -> XOAuthTokenSet | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text())
            return XOAuthTokenSet(
                access_token=str(data.get("access_token", "")),
                refresh_token=data.get("refresh_token"),
                token_type=str(data.get("token_type", "Bearer")),
                expires_at=_parse_datetime(data.get("expires_at")),
                granted_scopes=frozenset(data.get("granted_scopes", [])),
                obtained_at=_parse_datetime(data.get("obtained_at")) or datetime.now(UTC),
                authorized_user_id=data.get("authorized_user_id"),
                authorized_username=data.get("authorized_username"),
                identity_verified_at=_parse_datetime(data.get("identity_verified_at")),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise XOAuthError("TOKEN_STORAGE_INVALID", "OAuth token storage is invalid") from exc

    def save(self, token_set: XOAuthTokenSet) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": token_set.access_token,
            "refresh_token": token_set.refresh_token,
            "token_type": token_set.token_type,
            "expires_at": _iso(token_set.expires_at),
            "granted_scopes": sorted(token_set.granted_scopes),
            "obtained_at": _iso(token_set.obtained_at),
            "authorized_user_id": token_set.authorized_user_id,
            "authorized_username": token_set.authorized_username,
            "identity_verified_at": _iso(token_set.identity_verified_at),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)


class XOAuthService:
    def __init__(self, *, client_id: str | None = None, client_secret: str | None = None,
                 redirect_uri: str | None = None, scopes: set[str] | frozenset[str] | None = None,
                 expected_username: str | None = None, store: XOAuthTokenStore | None = None,
                 client: httpx.Client | None = None, token_endpoint: str = TOKEN_ENDPOINT):
        self.client_id = client_id or os.getenv("X_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("X_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri or os.getenv("X_OAUTH_REDIRECT_URI", "http://127.0.0.1:8976/oauth/callback")
        configured_scopes = scopes or set(filter(None, os.getenv("X_OAUTH_SCOPES", "").replace(",", " ").split()))
        self.scopes = frozenset(configured_scopes or DEFAULT_SCOPES)
        self.expected_username = expected_username if expected_username is not None else os.getenv("X_EXPECTED_USERNAME", "")
        self.store = store or XOAuthTokenStore()
        self.client = client or httpx.Client(timeout=20)
        self.token_endpoint = token_endpoint
        self._pending: dict[str, XOAuthAuthorizationRequest] = {}

    def begin_authorization(self) -> XOAuthAuthorizationRequest:
        if not self.client_id or not self.redirect_uri:
            raise XOAuthError("CONFIGURATION_ERROR", "X OAuth client ID and redirect URI are required")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = urllib.parse.urlencode({"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri, "scope": " ".join(sorted(self.scopes)), "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        request = XOAuthAuthorizationRequest(state, verifier, challenge, f"{AUTHORIZATION_ENDPOINT}?{query}", self.redirect_uri, self.scopes, datetime.now(UTC))
        self._pending[state] = request
        return request

    def validate_state(self, state: str) -> XOAuthAuthorizationRequest:
        request = self._pending.pop(state, None)
        if request is None:
            raise XOAuthError("AUTHORIZATION_REQUIRED", "OAuth callback state validation failed")
        return request

    def exchange_code(self, code: str, state: str) -> XOAuthTokenSet:
        if not code:
            raise XOAuthError("AUTHORIZATION_REQUIRED", "Authorization code is missing")
        request = self.validate_state(state)
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": request.redirect_uri, "code_verifier": request.code_verifier, "client_id": self.client_id}
        response = self._token_request(data)
        token_set = self._normalize_token_response(response)
        self.store.save(token_set)
        return token_set

    def refresh(self, token_set: XOAuthTokenSet | None = None) -> XOAuthTokenSet:
        token_set = token_set or self.store.load()
        if token_set is None or not token_set.refresh_token:
            raise XOAuthError("REFRESH_UNAVAILABLE", "A refresh token is not available")
        data = {"grant_type": "refresh_token", "refresh_token": token_set.refresh_token, "client_id": self.client_id}
        response = self._token_request(data)
        refreshed = self._normalize_token_response(response, previous=token_set)
        self.store.save(refreshed)
        return refreshed

    def get_valid_access_token(self, required_scopes: set[str] | frozenset[str]) -> str:
        token_set = self.store.load()
        if token_set is None:
            raise XOAuthError("AUTHORIZATION_REQUIRED", "OAuth authorization is required")
        if not token_set.has_scopes(required_scopes):
            raise XOAuthError("INSUFFICIENT_SCOPE", "OAuth token does not grant the required scopes")
        if token_set.status in {XOAuthTokenStatus.EXPIRING, XOAuthTokenStatus.EXPIRED, XOAuthTokenStatus.REFRESH_REQUIRED}:
            if not token_set.refresh_token:
                raise XOAuthError("REFRESH_UNAVAILABLE", "OAuth token is expired or expiring without a refresh token")
            token_set = self.refresh(token_set)
        if token_set.status in {XOAuthTokenStatus.MISSING, XOAuthTokenStatus.INVALID}:
            raise XOAuthError("AUTHORIZATION_REQUIRED", "OAuth access token is invalid")
        return token_set.access_token

    def verify_identity(self, token_set: XOAuthTokenSet | None = None) -> XOAuthAccountIdentity:
        token = token_set or self.store.load()
        if token is None:
            raise XOAuthError("AUTHORIZATION_REQUIRED", "OAuth authorization is required")
        if not token.has_scopes({"users.read"}):
            raise XOAuthError("INSUFFICIENT_SCOPE", "OAuth token does not grant users.read")
        access_token = self.get_valid_access_token({"users.read"})
        response = self.client.get(USERS_ME_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"})
        if response.status_code >= 400:
            raise XOAuthError("AUTHENTICATED_ACCOUNT_MISMATCH", "Authenticated X identity lookup failed")
        data = response.json().get("data", {})
        user_id, username = data.get("id"), data.get("username")
        if not user_id or not username:
            raise XOAuthError("TOKEN_RESPONSE_MALFORMED", "Authenticated X identity response was incomplete")
        identity = XOAuthAccountIdentity(str(user_id), str(username), data.get("name"), datetime.now(UTC))
        if self.expected_username and identity.username.casefold() != self.expected_username.lstrip("@").casefold():
            raise XOAuthError("AUTHENTICATED_ACCOUNT_MISMATCH", "Authenticated X username does not match the configured expected account")
        updated = XOAuthTokenSet(**{**_token_values(token), "authorized_user_id": identity.user_id, "authorized_username": identity.username, "identity_verified_at": identity.verified_at})
        self.store.save(updated)
        return identity

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        auth = None
        if self.client_secret:
            auth = (self.client_id, self.client_secret)
            data = {key: value for key, value in data.items() if key != "client_id"}
        try:
            response = self.client.post(self.token_endpoint, data=data, headers=headers, auth=auth)
        except httpx.HTTPError as exc:
            raise XOAuthError("TOKEN_ENDPOINT_UNAVAILABLE", "OAuth token endpoint is unavailable") from exc
        if response.status_code >= 400:
            category = "INVALID_REFRESH_TOKEN" if data.get("grant_type") == "refresh_token" else "AUTHORIZATION_REQUIRED"
            raise XOAuthError(category, "OAuth token exchange was rejected")
        try:
            return response.json()
        except ValueError as exc:
            raise XOAuthError("TOKEN_RESPONSE_MALFORMED", "OAuth token response was not valid JSON") from exc

    @staticmethod
    def _normalize_token_response(data: dict[str, Any], previous: XOAuthTokenSet | None = None) -> XOAuthTokenSet:
        access_token = data.get("access_token")
        if not access_token:
            raise XOAuthError("TOKEN_RESPONSE_MALFORMED", "OAuth token response did not contain an access token")
        expires_in = data.get("expires_in")
        expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in)) if expires_in is not None else None
        scopes = data.get("scope", previous.granted_scopes if previous else [])
        if isinstance(scopes, str): scopes = scopes.replace(",", " ").split()
        return XOAuthTokenSet(str(access_token), data.get("refresh_token") or (previous.refresh_token if previous else None), str(data.get("token_type", "Bearer")), expires_at, frozenset(scopes), datetime.now(UTC), previous.authorized_user_id if previous else None, previous.authorized_username if previous else None, previous.identity_verified_at if previous else None)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value: return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _token_values(token: XOAuthTokenSet) -> dict[str, Any]:
    return {"access_token": token.access_token, "refresh_token": token.refresh_token, "token_type": token.token_type, "expires_at": token.expires_at, "granted_scopes": token.granted_scopes, "obtained_at": token.obtained_at, "authorized_user_id": token.authorized_user_id, "authorized_username": token.authorized_username, "identity_verified_at": token.identity_verified_at}


# Explicit production-facing name for the shared adapter dependency.
XOAuthTokenProvider = XOAuthService
