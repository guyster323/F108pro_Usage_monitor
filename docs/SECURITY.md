# Security

QuotaDeck is not a password manager.

- Reuse already-logged-in CLI credentials. Never copy `auth.json` into the repo.
- Never write provider credential files. Token refresh belongs to the official CLI.
- Mask tokens, passwords, API keys, cookies, Authorization headers, email local
  parts and user-home paths in formatted application logs. Cookies are removed;
  other opaque secrets keep at most their final four characters for correlation.
- Never log provider response bodies, request headers, full account IDs or
  credential file contents.
- Session logs are user-private files, limited to 2 MiB plus three rollovers,
  and logs older than 14 days are removed (at most 80 diagnostic files retained).
- `*-crash.log` is written directly by Python's native fault handler. It does not
  dump Python locals or credential values, but stack frames can contain local
  file paths; review this file before sharing it outside your support channel.
- Read `state.vscdb` from a temporary copy, then delete the copy.
- `config.json` stores aliases, provider account IDs, and Cursor CSV/Admin
  binding metadata, but no access tokens, cookies, or Admin API keys. Treat
  it as private local data and never include it with shared logs.
- An optional Cursor Team Admin API key is stored only in Windows Credential
  Manager (`QuotaDeck/CursorAdminAPI`) or an injected test store. It is never
  written to config, the last-known-good usage cache, or diagnostic logs.
  Raw Admin API response bodies are not logged. Disconnecting the last
  Admin-bound account deletes that key; CSV-only disconnect does not.
- The LCD never shows a full email address by default.

## Cumulative local-history privacy

Cumulative mode reads retained CLI JSONL on this computer for Codex and Claude.
The scanner necessarily parses each candidate record locally, but the normalized
dataset keeps only timestamps, model names, token counters, stable hashed event
or session identifiers, and coverage metadata. Prompt and response bodies are
ignored, are not sent to QuotaDeck servers, and are not serialized into the
QuotaDeck cache. The dataset cache is memory-only and expires after a short TTL.

- Scans are limited to the expected `sessions`, `archived_sessions`, or
  `projects` roots. Symlinked roots/files and resolved paths outside those roots
  are skipped.
- A single JSONL line is bounded at 64 MiB. Oversized, malformed, or unreadable
  records make coverage partial instead of crashing the monitor or being
  presented as measured zero.
- The Grok session adapter invokes the official executable with an argument
  list and no shell. It applies a 5-second default timeout, a 256-session cap,
  and an 8 MiB stdout cap; command stderr and response bodies are not retained
  in returned diagnostic issues.
- Optional token-stats and ccusage adapters are opt-in (`QUOTADECK_TOKEN_STATS`,
  `QUOTADECK_CCUSAGE`, or `QUOTADECK_ENABLE_*`). They also run without a shell,
  default to an 8-second timeout, 8 MiB stdout, and 20,000 records, and never
  persist prompt/response bodies. Unknown or undated collector JSON is ignored
  instead of being guessed. Cursor tokens are never inferred from `state.vscdb`.
- `quotadeck prices` reads a dated bundled catalog. It does not send local usage
  or credentials to pricing pages at runtime.

## TLS and corporate CAs

Authenticated provider requests always verify certificates. QuotaDeck never
sets `verify=False`.

- The HTTPS client uses a verifying SSL context that also loads the OS trust
  store (Windows `CA`/`ROOT` stores, plus `SSL_CERT_DIR` when set).
- An explicit PEM bundle can be supplied with `QUOTADECK_CA_BUNDLE` or
  `SSL_CERT_FILE`. This is the supported path for a corporate inspection CA
  that is not yet in the OS store.
- A valid Cursor token with a broken trust chain is a usage-fetch failure, not
  a logged-out/stale session. Both `usage-summary` and
  `GetCurrentPeriodUsage` errors are retained. The GUI lock/status hover shows
  a masked, actionable reason. Hardware or site-specific corporate-CA
  verification still needs to be confirmed on the target network.

Local history is not cryptographically bound to the account that is signed in
now. Reusing one CLI profile after logout/login, or using both subscription and
API-key authentication in that profile, can mix retained records and historical
billing routes. Treat token totals and `LIST` price equivalents as this-device
observations, not account audits or invoices. Even when the analyzer has a full
365-day retained span, that proves neither continuous coverage nor measured
usage for every day; the LCD intentionally shows only the selected period.

Cursor account totals still require an administrator ledger or an attributable
export. The optional Admin API path never invents personal usage from
`state.vscdb`, never attaches team-wide events to another user, and never
turns a successful empty current-user result into a measured zero. A later
valid-empty page keeps any non-empty last-known-good cache and marks it stale.
Auth or network failures do not persist a candidate Admin key. Grok
external OTel is opt-in, begins only after configuration, and is stored under
the user's collector policy; the default runtime does not silently enable or
backfill it. See [CUMULATIVE_USAGE.md](CUMULATIVE_USAGE.md) for the complete
support and pricing limits.

See [TRAY_DIAGNOSTICS.md](TRAY_DIAGNOSTICS.md) for log retrieval and event meanings.
