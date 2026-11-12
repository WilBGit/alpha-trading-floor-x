#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from alpha_market_engine.auth.x_oauth import DEFAULT_SCOPES, XOAuthService, XOAuthTokenStatus


def main() -> int:
    load_dotenv()
    service = XOAuthService()
    token = service.store.load()
    configured = bool(service.client_id and service.redirect_uri and service.scopes)
    print(f"OAUTH CONFIGURATION PRESENT: {'YES' if configured else 'NO'}")
    print(f"TOKEN STORAGE PRESENT: {'YES' if token else 'NO'}")
    if token is None:
        print("TOKEN STATUS: MISSING")
        print("ACCESS TOKEN PRESENT: NO")
        print("REFRESH TOKEN PRESENT: NO")
        print(f"EXPIRES_AT: N/A")
        print("GRANTED SCOPES: N/A")
        print("REQUIRED PUBLISHING SCOPES SATISFIED: NO")
        print("IDENTITY VERIFIED: NO")
        print("AUTHORIZED USERNAME: N/A")
        print(f"EXPECTED USERNAME: {service.expected_username or '<not configured>'}")
        print("IDENTITY MATCH: NO")
        print("PUBLICATION AUTH READY: NO")
        return 0
    required = {"tweet.read", "tweet.write", "users.read"}
    identity_match = bool(token.authorized_username and service.expected_username and token.authorized_username.casefold() == service.expected_username.lstrip("@").casefold())
    ready = token.status in {XOAuthTokenStatus.VALID, XOAuthTokenStatus.EXPIRING} and token.has_scopes(required) and token.identity_verified_at is not None and (not service.expected_username or identity_match)
    print(f"TOKEN STATUS: {token.status.value}")
    print("ACCESS TOKEN PRESENT: YES")
    print(f"REFRESH TOKEN PRESENT: {'YES' if token.refresh_token else 'NO'}")
    print(f"EXPIRES_AT: {token.expires_at.isoformat() if token.expires_at else 'N/A'}")
    print(f"GRANTED SCOPES: {' '.join(sorted(token.granted_scopes))}")
    print(f"REQUIRED PUBLISHING SCOPES SATISFIED: {'YES' if token.has_scopes(required) else 'NO'}")
    print(f"IDENTITY VERIFIED: {'YES' if token.identity_verified_at else 'NO'}")
    print(f"AUTHORIZED USERNAME: {token.authorized_username or 'N/A'}")
    print(f"EXPECTED USERNAME: {service.expected_username or '<not configured>'}")
    print(f"IDENTITY MATCH: {'YES' if identity_match or not service.expected_username else 'NO'}")
    print(f"PUBLICATION AUTH READY: {'YES' if ready else 'NO'}")
    print(f"TOKEN STORAGE: {service.store.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
