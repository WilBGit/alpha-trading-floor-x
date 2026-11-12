"""
Alpha Trading Floor — X Publisher
====================================
Simple publish-only script. No LLM — the Hermes agent drafts the post.

Usage:
  python x_publisher.py publish "SPY up 0.4% at 754.78, QQQ down 0.3% at 717.65"
  python x_publisher.py publish "SPY up 0.4% at 754.78, QQQ down 0.3% at 717.65" --dry-run
  python x_publisher.py oauth-status
  python x_publisher.py refresh-token

State file: ~/.hermes/state/alpha_trading_floor/poster_state.json
Token file: ~/.hermes/state/alpha_trading_floor_x_oauth.json
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx

# ── Paths ─────────────────────────────────────────────────────────────────────

STATE_DIR = Path.home() / ".hermes" / "state" / "alpha_trading_floor"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "poster_state.json"
TOKEN_FILE = Path.home() / ".hermes" / "state" / "alpha_trading_floor_x_oauth.json"

# ── Load .env ─────────────────────────────────────────────────────────────────

def _load_env():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

X_CLIENT_ID = os.getenv("X_CLIENT_ID", "")
X_CLIENT_SECRET = os.getenv("X_CLIENT_SECRET", "")
X_EXPECTED_USERNAME = os.getenv("X_EXPECTED_USERNAME", "AlphaTradingF")
MAX_DAILY = int(os.getenv("ALPHA_DAILY_HARD_MAX", "1"))
MIN_SPACING = int(os.getenv("ALPHA_MIN_POST_SPACING_SECONDS", "72000"))
AUTO_POST_ENABLED = os.getenv("AUTO_POST_ENABLED", "false").strip().lower() == "true"
PUBLISH_MODE = os.getenv("PUBLISH_MODE", "DRY_RUN").strip().upper()

# ── OAuth ─────────────────────────────────────────────────────────────────────

def _load_token() -> dict | None:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def _save_token(data: dict):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(TOKEN_FILE)
    os.chmod(TOKEN_FILE, 0o600)


def _refresh_token() -> str:
    token = _load_token()
    if not token or not token.get("refresh_token"):
        print("ERROR: No refresh token. Run OAuth flow first.", file=sys.stderr)
        sys.exit(1)

    resp = httpx.post(
        "https://api.x.com/2/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": X_CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(X_CLIENT_ID, X_CLIENT_SECRET),
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"ERROR: Token refresh failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)

    new = resp.json()
    token.update({
        "access_token": new["access_token"],
        "refresh_token": new.get("refresh_token", token["refresh_token"]),
        "expires_at": (datetime.now(UTC) + timedelta(seconds=new.get("expires_in", 7200))).isoformat(),
        "granted_scopes": new.get("scope", " ".join(token.get("granted_scopes", []))).split(),
    })
    _save_token(token)
    print(f"Token refreshed. Expires: {token['expires_at']}")
    return token["access_token"]


def _get_access_token() -> str:
    token = _load_token()
    if not token:
        print("ERROR: No OAuth token. Run OAuth flow first.", file=sys.stderr)
        sys.exit(1)

    expires_at = token.get("expires_at")
    if expires_at:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if datetime.now(UTC) >= exp - timedelta(minutes=5):
            return _refresh_token()

    return token["access_token"]


def _verify_identity(access_token: str) -> dict:
    resp = httpx.get(
        "https://api.x.com/2/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"ERROR: Identity check failed: {resp.status_code}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()["data"]
    if X_EXPECTED_USERNAME and data.get("username", "").lower() != X_EXPECTED_USERNAME.lower():
        print(f"ERROR: Expected {X_EXPECTED_USERNAME}, got {data.get('username')}", file=sys.stderr)
        sys.exit(1)
    return data


# ── Proofreading ──────────────────────────────────────────────────────────────

def _proofread_text(text: str) -> tuple[bool, str]:
    """Block obvious malformed prices before the manual publish path."""
    if re.search(r"\$0{2,}(?:\.0+)?\b|\b(?:topped|above|below|at|is)\s+0{2,}(?:\.0+)?\b", text, re.I):
        return False, "malformed_zero_price"
    spy_match = re.search(
        r"\bSPY(?:\s+(?:is|at)|\s*:)\s+\$?(\d+(?:\.\d+)?)\b",
        text,
        re.I,
    )
    if spy_match and not 100 <= float(spy_match.group(1)) <= 10000:
        return False, "implausible_spy_price"
    if re.search(r"\b(?:oil|brent|wti|capex)\b.{0,35}\b(?:above|near|topped|to)\s+\$?0(?:\D|$)", text, re.I):
        return False, "implausible_zero_value"
    return True, ""


def _normalized_similarity_text(text: str) -> str:
    text = re.sub(r"\$?\d+(?:\.\d+)?%?", "<num>", text.casefold())
    return " ".join(re.findall(r"[a-z]+|<num>", text))


def _is_near_duplicate(text: str, recent_texts: list[str], threshold: float = 0.80) -> tuple[bool, float]:
    candidate = _normalized_similarity_text(text)
    best = 0.0
    for recent in recent_texts:
        score = SequenceMatcher(None, candidate, _normalized_similarity_text(recent)).ratio()
        best = max(best, score)
    return best >= threshold, best


def _read_post_file(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"post file not found: {path}")
    if path.stat().st_size > 4096:
        raise ValueError("post file is unexpectedly large")
    return path.read_text(encoding="utf-8").strip()


def _publication_gate() -> tuple[bool, str]:
    if not AUTO_POST_ENABLED:
        return False, "auto_post_disabled"
    if PUBLISH_MODE != "LIMITED_AUTO":
        return False, f"publish_mode_{PUBLISH_MODE.lower()}"
    return True, ""


# ── Publish ────────────────────────────────────────────────────────────────────

def _publish(text: str, *, dry_run: bool = False) -> dict:
    if dry_run:
        print(f"[DRY RUN] Would post ({len(text)} chars): {text}")
        return {"dry_run": True, "text": text, "char_count": len(text)}

    access_token = _get_access_token()
    identity = _verify_identity(access_token)

    resp = httpx.post(
        "https://api.x.com/2/tweets",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"text": text},
        timeout=20,
    )

    if resp.status_code not in (200, 201):
        print(f"ERROR: Post failed: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()["data"]
    print(f"Published: https://x.com/{identity.get('username', 'AlphaTradingF')}/status/{data['id']}")
    return {"id": data["id"], "text": data["text"]}


# ── State ──────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"posts": [], "last_post_at": None, "daily_count": 0, "daily_date": None}


def _save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)
    os.chmod(STATE_FILE, 0o600)


def _check_limits(state: dict) -> tuple[bool, str]:
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")

    if state.get("daily_date") != today:
        state["daily_date"] = today
        state["daily_count"] = 0

    if state["daily_count"] >= MAX_DAILY:
        return False, f"Daily limit reached ({state['daily_count']}/{MAX_DAILY})"

    if state.get("last_post_at"):
        last = datetime.fromisoformat(state["last_post_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        elapsed = (now - last).total_seconds()
        if elapsed < MIN_SPACING:
            remaining = int(MIN_SPACING - elapsed)
            return False, f"Min spacing not met ({remaining}s remaining)"

    return True, ""


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_publish(args):
    text = args.text.strip()
    if not text:
        print("ERROR: Post text cannot be empty.", file=sys.stderr)
        sys.exit(1)
    if len(text) > 280:
        print(f"ERROR: Post is {len(text)} chars (max 280).", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        allowed, gate_reason = _publication_gate()
        if not allowed:
            print(f"BLOCKED: Live publication disabled ({gate_reason})")
            return

    valid, reason = _proofread_text(text)
    if not valid:
        print(f"ERROR: Proofreading failed: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        _publish(text, dry_run=True)
        return

    state = _load_state()
    ok, reason = _check_limits(state)
    if not ok:
        print(f"SKIP: {reason}")
        return

    # Duplicate check
    content_hash = hashlib.sha256(text.casefold().strip().encode()).hexdigest()[:16]
    recent_hashes = [p.get("content_hash") for p in state.get("posts", [])[-3:]]
    if content_hash in recent_hashes:
        print("SKIP: Duplicate of recent post")
        return

    recent_texts = [p.get("text", "") for p in state.get("posts", [])[-10:]]
    near_duplicate, similarity = _is_near_duplicate(text, recent_texts)
    if near_duplicate:
        print(f"SKIP: Near-duplicate of recent post ({similarity:.0%} similarity)")
        return

    result = _publish(text, dry_run=args.dry_run)

    state["posts"].append({
        "text": text,
        "content_hash": content_hash,
        "result": result,
        "posted_at": datetime.now(UTC).isoformat(),
    })
    state["posts"] = state["posts"][-50:]
    state["last_post_at"] = datetime.now(UTC).isoformat()
    state["daily_count"] = state.get("daily_count", 0) + 1
    _save_state(state)


def cmd_oauth_status(args):
    token = _load_token()
    if not token:
        print("No OAuth token found.")
        return
    access_token = _get_access_token()
    token = _load_token() or token
    identity = _verify_identity(access_token)
    print(json.dumps({
        "status": "VALID",
        "username": identity.get("username"),
        "user_id": identity.get("id"),
        "expires_at": token.get("expires_at"),
        "scopes": token.get("granted_scopes", []),
    }, indent=2))


def cmd_refresh_token(args):
    _refresh_token()


def main():
    parser = argparse.ArgumentParser(description="Alpha Trading Floor — X Publisher")
    sub = parser.add_subparsers(dest="command")

    p_pub = sub.add_parser("publish", help="Publish a post to X")
    p_pub.add_argument("text", help="Post text (max 280 chars)")
    p_pub.add_argument("--dry-run", action="store_true", help="Preview without publishing")

    p_file = sub.add_parser("publish-file", help="Publish exact UTF-8 text from a file")
    p_file.add_argument("path", help="Path to text file (max 4096 bytes)")
    p_file.add_argument("--dry-run", action="store_true", help="Preview without publishing")

    sub.add_parser("oauth-status", help="Show OAuth token status")
    sub.add_parser("refresh-token", help="Force OAuth token refresh")

    args = parser.parse_args()
    if args.command == "publish":
        cmd_publish(args)
    elif args.command == "publish-file":
        try:
            args.text = _read_post_file(Path(args.path))
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        cmd_publish(args)
    elif args.command == "oauth-status":
        cmd_oauth_status(args)
    elif args.command == "refresh-token":
        cmd_refresh_token(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
