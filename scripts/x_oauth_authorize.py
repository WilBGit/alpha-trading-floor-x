#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import argparse
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv

from alpha_market_engine.auth.x_oauth import XOAuthError, XOAuthService


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Authorize the Alpha Trading Floor X OAuth 2.0 user context")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    service = XOAuthService()
    if not service.client_id:
        print("AUTHORIZATION: FAIL")
        print("ERROR: CONFIGURATION_ERROR (X_CLIENT_ID is not configured)")
        return 2
    parsed = urllib.parse.urlparse(service.redirect_uri)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({key: values[0] for key, values in params.items() if values})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Authorization received. You may close this window.")

        def log_message(self, *_args):
            return

    request = service.begin_authorization()
    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 80), CallbackHandler)
    print("Open this authorization URL in a browser:")
    print(request.authorization_url)
    if not args.no_browser:
        webbrowser.open(request.authorization_url)
    print("Waiting for the OAuth callback...")
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()
    if not result.get("code") or not result.get("state"):
        print("AUTHORIZATION: FAIL")
        print("ERROR: AUTHORIZATION_REQUIRED (callback did not contain an authorization code)")
        return 3
    try:
        token = service.exchange_code(result["code"], result["state"])
        identity = service.verify_identity(token)
    except XOAuthError as exc:
        print("AUTHORIZATION: FAIL")
        print(f"ERROR: {exc.category}")
        return 4
    print("AUTHORIZATION: SUCCESS")
    print(f"AUTHORIZED USER ID: {identity.user_id}")
    print(f"AUTHORIZED USERNAME: {identity.username}")
    print(f"EXPECTED USERNAME: {service.expected_username or '<not configured>'}")
    print(f"IDENTITY MATCH: {'YES' if not service.expected_username or identity.username.casefold() == service.expected_username.lstrip('@').casefold() else 'NO'}")
    print(f"GRANTED SCOPES: {' '.join(sorted(token.granted_scopes))}")
    print(f"TOKEN STATUS: {token.status.value}")
    print(f"REFRESH TOKEN PRESENT: {'YES' if token.refresh_token else 'NO'}")
    print(f"TOKEN STORAGE: {service.store.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
