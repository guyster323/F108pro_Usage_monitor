# AI Usage Engine Audit

Audit date: 2026-09-12 (Asia/Seoul)  
Scope: Cursor, Grok Build, normalized token usage, reported/API-equivalent cost, USD/KRW FX, account aggregation, storage, API/UI integration, and tests.

## Existing Architecture

QuotaDeck is a Python 3.11+ desktop/CLI application. Account discovery produces `AccountRef` objects, quota providers return `UsageSnapshot`/`UsageWindow`, and the separate `quotadeck.usage` package scans cumulative token observations into `UsageDataset` and `UsageReport`. `CumulativeUsageService` adapts the cumulative report into `CumulativeSnapshot` for the GUI, CLI, renderer, scheduler, and F108 LCD output.

The cumulative pipeline is currently:

```text
AccountRef
  -> provider-specific collector/import/local history
  -> UsageObservation(TokenUsage)
  -> UsageDataset (dedupe + coverage)
  -> UsageReport (daily/monthly/model aggregation)
  -> optional list-price estimate
  -> CumulativeSnapshot
  -> CLI / GUI / renderer
```

Quota and cumulative usage are separate at the top-level through `MetricMode.QUOTA` and `MetricMode.CUMULATIVE`. Quota code lives primarily under `quotadeck.providers`; token history, analytics, and pricing live under `quotadeck.usage`. This separation is sound and should be preserved.

There is no application usage database or migration layer. Transcript-derived datasets are retained only in a bounded in-memory TTL cache. Provider CLI/application history and token-stats cache files are the durable sources. App preferences and account aliases are stored in `%APPDATA%/QuotaDeck/config.json`; account entries store identifiers and source paths, not secrets.

## Current Provider Support

| Provider | Quota/rate limit | Token usage | Cost |
| --- | --- | --- | --- |
| Codex | OAuth endpoint with app-server fallback; API-key quota is unavailable | Native retained JSONL, optional token-stats, ccusage fallback | Static official API list-price catalog, gated to explicit API billing |
| Claude | Official usage endpoint for supported OAuth accounts | Native retained JSONL, optional token-stats, ccusage fallback | Static official API list-price catalog, gated to explicit API billing |
| Cursor | `usage-summary`, then DashboardService fallback, authenticated by existing Cursor session | Attributable CSV/JSON/token-stats cache import only | Base-model catalog exists, but the cumulative service normally treats subscriptions as unpriced |
| Grok Build | Billing/quota endpoint for stored Grok session; API-key quota unavailable | Strict `grok usage <session-id>` parser and external OTel adapter exist, but the application service does not load them | Catalog entries and `costUsdTicks` parsing exist, but no normalized reported-cost output reaches the UI/API |

`grok.com` consumer Web Chat is not a supported cumulative source and must remain explicitly unsupported unless xAI provides an official export/API.

## What Works

- `TokenUsage` keeps uncached input, cached input, cache writes, output, and reasoning-output metadata distinct; reasoning is not double-counted in totals.
- Quota percentages and cumulative token counts use different contracts and different display modes.
- Local parsers are bounded, deduplicate stable events, reject malformed/oversized inputs, and avoid retaining prompt/response bodies.
- Cursor `state.vscdb` is used only to discover authentication/quota context; its `tokenCount` is not treated as a token ledger.
- Cursor export parsing requires account attribution and a real date/timestamp.
- Grok's official usage JSON has a strict parser with inclusive-input normalization, model/turn consistency checks, and `costUsdTicks` support. The existing fixture/parser nevertheless predates parts of the current official envelope: current source uses `endedAt`, `primaryModelId`, `costIsPartial`, and `usageIsIncomplete`; the project fixture's `promptId` is not an authoritative identifier.
- External commands use `shell=False`, bounded output/time, a reduced child environment, discarded stderr, and generic failure messages.
- Pricing uses `Decimal`, dated source URLs, provider/model normalization, cache-specific rates, and all-or-nothing validation.
- Account discovery already supports separate Cursor IDE/CLI sessions and stable provider/account keys.
- UI rendering already supports USD and KRW presentation and keeps model detail outside the small LCD surface.

## What Is Broken

- Cursor does not initiate the token-stats `cursor sync` flow. It only reads an already-created cache/import, so normal authenticated users receive `unsupported` until they manually prepare files. The currently documented token-stats JSON report is also undated; the authoritative integration artifact for calendar totals is its synced CSV cache.
- The Cursor CSV reader does not recognize the current token-stats v1-v3 export headers (`Input (w/ Cache Write)`, `Input (w/o Cache Write)`, `Cache Read`, `Output Tokens`). It also refuses the normal active-account `usage.csv`, so even a successful upstream sync is usually unusable.
- Cursor cache discovery does not expose a complete first-class multi-account inventory/aggregate contract. The active compatibility file (`usage.csv`) can overlap named account caches and needs explicit deduplication/ownership rules.
- Cursor credentials are read from local Cursor state for quota calls, while token-stats keeps separately supplied web-session credentials. QuotaDeck has no secure credential-store abstraction for credentials it owns; therefore it must not import/copy token-stats credentials.
- Grok session discovery and official-command parsing are disconnected from `UsageService`; `CumulativeUsageService.snapshot()` unconditionally rejects Grok.
- The current Grok scan intentionally emits no observations, so parsed token totals and reported cost cannot reach normalized per-session/provider totals.
- No incremental on-disk Grok cache exists; every scan invokes the CLI for every discovered session. Official command output is pretty JSON and `grok usage <session-id> <turn>` is also supported.
- `UsageObservation` has no `account_id`, turn identifier, optional reported cost, or explicit nullable unknown fields. Account scope exists outside the record and is therefore hard to aggregate safely.
- `costUsdTicks` is parsed but not exposed as `reported_cost_usd`; reported and API-equivalent cost do not share a public normalized result.
- API-equivalent cost is incorrectly gated to explicitly API-billed history in the display service. That prevents showing the requested hypothetical equivalent for subscription usage, even though it is not reported spend.
- KRW conversion is a manually entered scalar only. There is no fetched rate, source/as-of metadata, last-known-good cache, staleness rule, or fallback chain.
- No persistent usage/history schema exists. This avoids secret leakage but also prevents incremental source cursors, last-known FX, and durable deduplication.
- Collector and UI contracts are coupled through `CumulativeSnapshot`; there is no provider/account/global aggregate API independent of the LCD view.
- Baseline test run with an isolated temp directory: 361 passed, 1 skipped, 16 failed. Most failures are caused by missing Windows `tzdata` (not declared in dependencies). One Cursor test depends on the wall-clock date and changed behavior after midnight. The default pytest temp/cache roots on this host also have unrelated ACL errors.

## Missing Features

- Live token-stats Cursor synchronization using its authenticated session and account-specific cache files.
- Safe Cursor multi-account inventory, per-account totals, provider total, and global total.
- Grok official CLI collection wired into the application, with version/capability detection and documented fallback order.
- Incremental Grok session cache keyed by session identity and source modification state.
- A normalized record that carries account, provider, model, timestamp/date, session/turn, nullable token categories, reported cost, and source provenance without credentials.
- Separate `reported_cost_usd`, `api_equivalent_cost_usd`, and `api_equivalent_cost_krw` semantics.
- FX rate acquisition with source, quote time/date, staleness, last-known-good persistence, and manual fallback.
- Machine-readable provider/account/global aggregation independent of the existing display snapshot.
- Fixtures for Grok tick conversion and official JSON envelopes, Cursor named-account cache behavior, cost separation, and FX fallback.

## Reusable Components

- `TokenUsage`, `UsageObservation`, `UsageCoverage`, `UsageDataset`, and analytics aggregation.
- `CollectorSettings`, `CollectorAttempt`, strict mapping/parsing helpers, and bounded subprocess execution.
- Cursor authentication discovery and quota clients, provided secrets remain `repr=False` and are never serialized.
- Cursor CSV/JSON and token-stats payload parsers.
- Grok usage envelope/summary/turn parser and safe session-ID discovery.
- Provider-aware pricing catalog, exact `Decimal` arithmetic, and model alias resolution.
- Account configuration/discovery and current GUI/CLI/renderer period/currency controls.
- Existing masking, diagnostics exclusion, and generic error reporting policies.

## Required Refactoring

1. Extend the normalized usage contract compatibly with account/session/turn identity and nullable reported cost. Preserve `TokenUsage` arithmetic for known counters and never fabricate unavailable categories.
2. Add a service-level aggregate result for per-account, per-provider, and global totals. Do not overload quota `UsageSnapshot`.
3. Add a Cursor sync adapter that calls the installed/configured token-stats executable, reads only its normalized cache/export, keeps named accounts isolated, and never reads or copies its credential file. Parse current CSV headers by name: uncached input is `Input (w/o Cache Write)` and cache-write is the non-negative difference from `Input (w/ Cache Write)`.
4. Connect Grok discovery/parser to `UsageService`; add a bounded incremental cache and explicitly label session-derived coverage limitations. Prefer the official `grok usage` command; add structured-headless and source-verified JSONL fallback only when their schemas are proven.
5. Split reported cost from hypothetical API-equivalent cost. API-equivalent estimates may be shown for subscription records when token/model/category coverage is complete; they must never be labeled as actual spend.
6. Add an FX service and cache whose values carry rate, source, fetched/as-of time, and stale/fallback status. No API key or credential may enter config/history/logs.
7. Keep the existing LCD adapter as a consumer of the normalized engine instead of embedding provider collection decisions in the renderer.
8. Add `tzdata` for portable named-zone behavior on Windows and eliminate tests that depend on the current wall-clock date.

## Implementation Plan

1. Independently verify token-stats Cursor behavior, xAI Grok Build schemas/commands, pricing sources, and FX providers against current primary/source-level references.
2. Implement normalized nullable cost/account metadata and aggregate APIs without breaking existing callers.
3. Implement Cursor token-stats sync/cache ingestion and multi-account aggregation; keep current import support as a deterministic fallback.
4. Implement Grok official CLI collection with incremental cache and strict conversion fixtures; expose session/account/provider results only at the granularity the source proves.
5. Implement API-equivalent pricing independently of billing mode and retain a separate provider-reported cost field.
6. Implement automatic USD/KRW lookup with last-known-good and manual fallback, plus source/as-of metadata.
7. Wire provider results into `UsageService`, the compatibility snapshot, CLI, and GUI labels while preserving quota mode.
8. Update security/provider/collector documentation and add focused fixtures/tests.

## Verification Plan

- Unit-test every nullable token/cost field and ensure unknown never becomes zero.
- Verify Cursor `state.vscdb` token counters are never consulted and credentials never appear in `repr`, output, cache, exceptions, or history.
- Simulate token-stats sync success, missing executable, timeout, invalid JSON, named accounts, active `usage.csv` overlap, and stale cache fallback.
- Validate Grok discovery, option-safe session IDs, official CLI precedence, command timeout/output bounds, unchanged-session cache hits, changed-session refresh, and fallback ordering.
- Fixture-test `costUsdTicks / 10^10` with exact `Decimal` values and reject negative/non-integral/overflow inputs.
- Test reported-cost and API-equivalent-cost independence for API and subscription accounts.
- Test pricing for every catalog row, unknown model/category refusal, aliases, cache rates, and exact rounding.
- Test live FX success, malformed response, timeout, stale/fresh last-known-good cache, and manual fallback; assert source/as-of metadata.
- Test per-account, provider-total, and global-total aggregation without double counting.
- Run focused usage/pricing/FX/provider tests, then the full pytest suite using a workspace-local basetemp and disabled pytest cache on this Windows host.
- Perform a credential-pattern scan and `git diff --check` before handoff.

## Post-implementation status

The planned refactoring is now present in the working tree. `cursor_export.py`
parses token-stats dashboard CSV v1-v3, performs exclusive input/cache-write
normalization, supports named-account isolation, and optionally invokes the
bounded `token-stats cursor sync --json` adapter. `grok.py` invokes only the
official `grok usage <session-id>` command, caches credential-free results by
session/source fingerprint, converts positive `costUsdTicks` using 10^10 ticks
per USD, and exposes explicit unsupported coverage for grok.com Web Chat.

`normalized.py` and `engine.py` provide nullable provider-neutral records plus
account, provider, and global rollups. `reported_cost_usd` is never reused as
API spend; `estimate_api_equivalent_cost` is independent of subscription/API
billing mode and KRW conversion carries FX provenance and fallback metadata.
The machine-readable contract is available through `quotadeck usage-engine
--json`.

Focused usage/pricing/FX tests pass (108 tests); the full suite passes 416 tests
with one pre-existing skip. Live authenticated Cursor token-stats and Grok CLI
sessions were not available in this environment, so end-to-end provider calls
remain source/fixture verified rather than credential-tested.

## Verified references

- Cursor token export implementation: https://github.com/Annihilater/token-stats
- Cursor documented team/admin usage API: https://cursor.com/docs/account/teams/admin-api
- Grok Build CLI source and usage command: https://github.com/xai-org/grok-build
- xAI API pricing catalog: https://docs.x.ai/developers/pricing
- Treasury reporting exchange-rate dataset: https://fiscaldata.treasury.gov/currency-exchange-rates-converter/
