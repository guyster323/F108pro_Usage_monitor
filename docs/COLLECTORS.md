# Optional usage collectors

QuotaDeck 0.2.3 can read **token-stats** as a preferred optional cumulative
collector and **ccusage** as a validation/fallback boundary. Neither tool is
required. Native Codex/Claude parsers remain the scoped fallback, and Cursor
stays `N/A` until an attributable export exists.

## Priority

```text
token-stats JSON / import   (preferred when dated and scoped)
        ↓ missing / timeout / unknown schema
native Codex / Claude parsers
        ↓ empty native history
ccusage daily JSON          (Codex/Claude only; never Cursor)
```

ccusage never blends with another source. If overlapping daily totals disagree,
QuotaDeck keeps the preferred observations and marks coverage `PARTIAL`.

## Trust boundary

Install the CLIs yourself if you want them. QuotaDeck does not vendor them.

| Source | Used when | Refused when |
| --- | --- | --- |
| token-stats | Dated rows, `--client` matches the card, token categories parse | No executable, timeout, oversized JSON, month-only aggregates, undated `entries` |
| QuotaDeck import | `schema = quotadeck.collector.v1` with timestamps | Prompt/response-only rows, unknown schema |
| ccusage | Documented daily JSON for one Codex or Claude home | Cursor, mixed multi-agent rows, missing dates |
| Cursor export | `usage.<account>.csv` or JSON with account + date + tokens | `state.vscdb`, `auth.json`, generic `usage.csv` without account |

Set an explicit executable or import to enable live collection:

```powershell
$env:QUOTADECK_TOKEN_STATS = "C:\path\to\token-stats.exe"
$env:QUOTADECK_ENABLE_TOKEN_STATS = "1"
$env:QUOTADECK_CCUSAGE = "C:\path\to\ccusage.exe"
$env:QUOTADECK_ENABLE_CCUSAGE = "1"
$env:QUOTADECK_COLLECTOR_IMPORT = "C:\path\to\collector.json"
$env:QUOTADECK_CURSOR_EXPORT = "C:\path\to\usage.work.csv"
```

Without those variables QuotaDeck never spawns a collector. When it does, the
child is started with `shell=False`, a timeout (default 8s), an 8 MiB stdout
cap, and a 20,000-record cap. Stderr is discarded.

## Import document

```json
{
  "schema": "quotadeck.collector.v1",
  "source": "token-stats",
  "observations": [
    {
      "provider": "codex",
      "account": "work",
      "model": "gpt-5.6-sol",
      "observed_at": "2026-09-11T01:00:00Z",
      "session_id": "s1",
      "event_id": "e1",
      "input_tokens": 100,
      "output_tokens": 20,
      "cached_input_tokens": 10,
      "cache_write_tokens": 0,
      "reasoning_output_tokens": 5
    }
  ]
}
```

Reasoning is a subset of output and is never added twice. Prompt and response
bodies are ignored and are not written to the memory-only usage cache.

token-stats `models --json` rows are accepted only when each used row also has
`date`, `day`, `timestamp`, or `observed_at`. The documented undated
`groupBy/entries` object cannot drive calendar THIS/AVG, so QuotaDeck falls
back instead of inventing days.

## Cursor

Cursor cumulative usage is enabled only from exported or server-derived
collector data with account and time attribution. Typical token-stats cache
files live under `%APPDATA%\tokscale\cursor-cache\` as `usage.<account>.csv`.
A file named only `usage.csv` is refused unless every row names an account.
`state.vscdb` remains a remaining-limit login source, not a token ledger.
