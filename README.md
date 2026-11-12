# Alpha Trading Floor X Auto Content Publisher

[![CI](https://github.com/WilBGit/alpha-trading-floor-x/actions/workflows/ci.yml/badge.svg)](https://github.com/WilBGit/alpha-trading-floor-x/actions/workflows/ci.yml)

A small, auditable publication boundary for Alpha Trading Floor posts on X. Drafting and market research happen outside this repository. This project accepts a completed post, proofreads it, applies duplicate and cadence controls, verifies the authenticated X identity, and only then calls the X API v2 publishing endpoint.

## What is included

- `scripts/x_publisher.py`: publish, publish-file, OAuth-status, and token-refresh commands.
- `scripts/x_topic_policy.py`: deterministic rotation across SPY, QQQ, and FINVIZ stock lanes.
- `scripts/x_oauth_authorize.py` and `scripts/x_oauth_status.py`: PKCE authorization and redacted readiness checks.
- `src/alpha_market_engine/auth/x_oauth.py`: OAuth token lifecycle, scope checks, identity verification, and owner-only token storage.
- Guardrail tests for proofreading, duplicate prevention, dry-run behavior, and topic rotation.

## Safety boundary

The repository is publication code, not an authorization to post. The default configuration is `AUTO_POST_ENABLED=false` and `PUBLISH_MODE=DRY_RUN`. Live publication requires an explicit operator-managed environment, a valid OAuth token stored outside Git, the expected account identity, the required `tweet.write` scope, and the configured cadence limits.

The publisher refuses empty or oversized text, malformed price claims, implausible SPY prices, exact duplicates, near duplicates, posts over the daily limit, and posts inside the minimum spacing interval. It never prints access or refresh token values.

## Local use

Use a virtual environment and install `requirements.txt`. Configure values only through an environment manager or a local ignored `.env` file. The supported state paths are under `~/.hermes/state/alpha_trading_floor`; they are not repository data.

Preview without any network write:

    python scripts/x_publisher.py publish "SPY is $742.90. Watch support and yields." --dry-run

Run the deterministic guardrail tests when the test dependencies are available:

    python -m unittest discover -s tests -v

No live X post is performed by the repository validation process.

## Account and content rules

- Publish only to the explicitly verified Alpha Trading Floor X account.
- Keep posts within X's 280-character limit.
- Preserve source facts exactly once they have been verified.
- Do not use this boundary for financial advice, trade execution, or unattended strategy changes.
