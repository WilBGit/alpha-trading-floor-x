# Security

## Credential handling

Never commit X client secrets, bearer tokens, access tokens, refresh tokens, OAuth callback data, private keys, or local `.env` files. The token store defaults to `~/.hermes/state/alpha_trading_floor_x_oauth.json`, is written with owner-only permissions, and is intentionally outside the repository.

Use a secret manager or environment injection in scheduled environments. If a secret is exposed, revoke it at the provider immediately, replace it outside Git, and inspect the repository history before restoring service.

## Publication controls

The default is dry-run and live publication is fail-closed. Before any live write the operator must verify the intended X username, OAuth scopes, token status, post text, daily limit, and minimum spacing. The publisher checks the authenticated `/2/users/me` identity before posting.

Do not paste credentials, authorization codes, or provider responses containing secrets into chat, issues, logs, or pull requests. Diagnostics must contain statuses and categories, not token values or full response bodies.

## Data exclusions

Ignored content includes secrets, OAuth state, runtime state, logs, caches, databases, generated media, and virtual environments. Market data and drafts should be treated as untrusted input until independently verified.
