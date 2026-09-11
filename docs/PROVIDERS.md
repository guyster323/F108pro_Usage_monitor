# Providers

QuotaDeck never writes provider credential files. Tokens stay with the official
CLI or app. Remaining-limit queries and cumulative local-history scans are
different data paths and have different support boundaries.

## Remaining-limit mode

| Provider | Source | Credential path | Remaining-limit API |
| --- | --- | --- | --- |
| Codex | CLI | `$CODEX_HOME/auth.json`, `~/.codex`, extra homes, or process `OPENAI_API_KEY` with retained local history | OAuth: `GET chatgpt.com/backend-api/wham/usage`, fallback `codex app-server`; API key: quota N/A |
| Cursor | App | `%APPDATA%\Cursor\User\globalStorage\state.vscdb` | `GET cursor.com/api/usage-summary`, fallback api2 RPC |
| Cursor | CLI | `%APPDATA%\Cursor\auth.json` | same |
| Claude | CLI | `~/.claude/.credentials.json` or retained `projects/**/*.jsonl` | OAuth-only: `GET api.anthropic.com/api/oauth/usage`; API key/local-only/ambiguous auth: quota N/A |
| Grok | CLI | `$GROK_HOME/auth.json`, `config.toml` model auth, `XAI_API_KEY`, or retained sessions | Active session: `GET cli-chat-proxy.grok.com/v1/billing?format=credits`; API/ambiguous/local-only: quota N/A |

Cursor HUD windows follow the dashboard bars, not included-spend cents:

- `AUTO` ← `individualUsage.plan.autoPercentUsed` (Cursor Models: Auto / Composer / Cursor Grok)
- `OTHER` ← `individualUsage.plan.apiPercentUsed` (Other Models)

`plan.used / plan.limit` is a cents bucket and does not match those percentages.

The settings app lists each signed-in source and lets you enable only the
accounts that should rotate on the F108 Pro. The same Cursor user found in both
App and CLI is shown once (App first).
Claude and Grok were implemented from documented APIs and fixtures. Live verification
depends on a logged-in CLI on the user's machine.

## Cumulative-usage mode

| Provider | Implemented source | Tokens / model | Daily and monthly comparison | Selected-period cost pair |
| --- | --- | --- | --- | --- |
| Codex | Retained `sessions` and `archived_sessions` JSONL in the selected home | Supported | Supported for the timestamps actually retained | `LIST` equivalent only for explicit API-key billing |
| Claude Code | Retained `projects/**/*.jsonl` response metadata | Supported | Supported for the timestamps actually retained | `LIST` equivalent only for explicit API-key billing |
| Cursor | Attributable token-stats / server Usage export only; `state.vscdb` is not a ledger | Conditional | Conditional | N/A unless the export is complete and API-priced |
| Grok Build | Official `grok usage <session-id>` inventory; caller-supplied external OTel v1 adapter | Exact per-session totals; per-model data is conditional | Default account card is N/A; conditional only from retained, timestamped OTel records | N/A on the account card |

“Supported” means observations retained by this local profile/device. It does
not mean complete account, organization, or invoice coverage. The analyzer uses
at most 365 days of history, and records outside that retained window do not
prove measured zero inside it. That retained total/span and model names are not
drawn on the LCD: the small display is reserved for the selected-period bar and
large `THIS`/`AVG` values.

The period contract is calendar-based in the current system time zone:

- **Daily:** `THIS` is today; `AVG` is the prior completed-day average and is
  enabled after at least seven completed days.
- **Monthly:** `THIS` is the current month-to-date; `AVG` is the average of
  prior completed calendar months and is enabled after at least one. The first
  retained month is excluded when it is partial. Later empty completed months
  are included as zero.

The LCD bar is `THIS tokens / AVG tokens × 100`. Its fill saturates at 100%,
but the numeric label preserves over-average meaning and reaches `300%+`.
Normal captions contain only the period and baseline (`M AVG` or `D AVG`); the
LCD omits currency labels and Hangul. Insufficient, partial, and unavailable
histories instead use
`M/D BUILD`, `M/D PARTIAL`, and `M/D N/A` so they cannot resemble a measured
average.
`THIS` and `AVG` token values use one shared compact unit so large pairs remain
directly comparable (for example, `0.6B / 1.2B`). Model information is GUI-only:
the account row shows the top two models for the selected period, while its
tooltip lists the full selected-period breakdown. The CLI retains full model
inspection through `quotadeck usage --cumulative --models` (or
`quotadeck cumulative --models`).

Cursor exposes administrator usage data, and OpenAI/Anthropic also have
organization or administrator usage/cost products, but QuotaDeck 0.2 does
**not** collect administrator keys or call those APIs. Do not configure an
admin secret expecting it to enable the LCD card. 0.2.3 may enable a Cursor
cumulative card only from an attributable exported/server-derived collector
file (typically token-stats `usage.<account>.csv`). See
[COLLECTORS.md](COLLECTORS.md). token-stats is the preferred optional
collector for Codex/Claude/Cursor; native parsers remain the fallback;
ccusage is a validation/fallback boundary and is never blended.

`grok usage` is authoritative for one persisted session, including recorded
cost ticks when present. It is not an account ledger: resumed and forked
sessions can contain inherited overlap, and its turn rows do not provide usage
timestamps. QuotaDeck therefore never sums those session totals and never maps
the whole session to `updatedAt`. The external OTel v1 adapter accepts
timestamped request events only after a user has explicitly configured and
retained them in their own collector/storage; OTel is off by default, cannot
backfill earlier usage, and is not a built-in account-wide collector in 0.2.

Codex and Claude model totals come from the model and token counters present in
their retained JSONL records. The scanner parses those records locally but
keeps only timestamps, model names, stable event/session identifiers, and token
counters in the normalized dataset. It does not persist prompt or response
content in QuotaDeck's cache.

See [CUMULATIVE_USAGE.md](CUMULATIVE_USAGE.md) for the exact daily/monthly
calculations, five reaction bands, price snapshot, and all-or-nothing cost rules.

The dated `quotadeck prices` catalog contains API list-price rows for OpenAI,
Anthropic, Cursor, and xAI. Catalog coverage is independent of usage-ledger
support: Cursor rows are base-model reference prices and Grok session data is
not promoted to an account cost card. A price row therefore never means that
QuotaDeck implemented an administrator/account ledger for that provider.

For eligible API-key histories, costs are calculated separately for `THIS` and
`AVG` from each period's actual model/token mix. The displayed pair is
all-or-nothing: an unknown model, token category, billing route, custom
endpoint, partial scan, or incomplete side makes both values `N/A`. Settings
select KRW or USD. KRW uses a manually entered exchange rate that defaults to
1,400 KRW/USD; it is never fetched or refreshed from the internet. The GUI
labels this selection `KRW (10,000 won)` and shows the scale legend
`1K = 10 million won · 1M = 10 billion won · 1B = 10 trillion won`. LCD cost
values use only ASCII `K/M/B`, for example `1K / 2K` in the KRW setting or
`0.4K / 0.8K` in USD; the LCD does not repeat the selected currency.

## Authentication classification and attribution

API-key values are never copied into account metadata or config. Codex explicit
API mode, a key stored in that home's `auth.json`, or a process `OPENAI_API_KEY`
overrides retained OAuth for auth-mode classification. The process-global key is
assigned only to explicitly selected `CODEX_HOME` (otherwise the default
`~/.codex`) and never fanned out to extra homes. Without a file-backed identity,
retained `sessions` or `archived_sessions` are also required. In API mode,
QuotaDeck discards inactive OAuth tokens and identity claims instead of
attributing the API key to that OAuth account. Claude is API-billed only when retained history and
`ANTHROPIC_API_KEY` are detected without OAuth, cloud-provider, or auth-token
signals. Mixed Claude credential contexts are unpriced and skip subscription
quota requests. For Grok Build, a usable per-model `api_key`, populated `env_key`,
or model `auth_provider` can outrank stored session auth; because a future CLI
`--model` selection is unknown, that combination is marked ambiguous and
unpriced. A non-expired stored session outranks fallback `XAI_API_KEY`, while an
expired session does not mask that environment key.

Authentication classification describes the credentials available **now**;
retained history is not cryptographically bound to that current account or
billing route. Logging out, signing in as another account, or alternating a
subscription and API key in the same profile can mix records. Consequently,
local totals and `LIST $…` equivalents are device observations, not audit-grade account
attribution or invoices.

## Normalized contracts

Remaining-limit snapshot fields: `provider`, `account_id`, `display_name`, `plan`,
`windows[]` (`id`, `label`, `used_percent`, `remaining_percent`, `resets_at`),
`status`, `fetched_at`.

Cumulative datasets normalize provider, model, UTC timestamp, mutually
exclusive token categories, stable event/session IDs, and coverage/limitation
metadata. Display snapshots select a daily or monthly `THIS`/`AVG` comparison,
selected-period model totals, status, and an optional all-or-nothing cost pair.
The settings schema is **config v5** and persists the selected period, currency,
and manual USD-to-KRW rate.
