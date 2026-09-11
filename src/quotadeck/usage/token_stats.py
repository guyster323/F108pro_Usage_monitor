"""Optional token-stats collector adapter.

token-stats is the preferred optional cumulative collector when it can be
discovered and when its JSON has calendar dates plus mutually exclusive token
categories. The documented models JSON (``groupBy`` + ``entries``) is accepted
only when every used row carries a date or timestamp. Month-only aggregates
without a day are refused so THIS/AVG cannot be invented.

Live CLI execution is best-effort. Timeout, oversized output, unknown schema,
or a missing executable fall back to native parsers. Prompt/response bodies
are never retained.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quotadeck.usage.collectors import (
    CollectorAttempt,
    CollectorName,
    CollectorSettings,
    CollectorStatus,
    coverage_for,
    map_client_to_provider,
    observation_from_row,
    parse_quotadeck_import,
)
from quotadeck.usage.models import UsageDataset, UsageObservation, UsageSourceKind
from quotadeck.usage.process import (
    decode_json_bytes,
    minimal_child_environment,
    resolve_optional_executable,
    run_bounded_command,
)


TOKEN_STATS_CLIENTS = {
    "codex": "codex",
    "claude": "claude",
    "cursor": "cursor",
    "grok": "grok",
}


def discover_token_stats(
    settings: CollectorSettings | None = None,
) -> Path | None:
    if settings and settings.token_stats_executable is not None:
        return settings.token_stats_executable
    found = resolve_optional_executable(
        "token-stats",
        env_var="QUOTADECK_TOKEN_STATS",
    )
    if found is not None:
        return found
    return resolve_optional_executable("tokscale", env_var="QUOTADECK_TOKSCALE")


def _iter_candidate_rows(payload: object) -> tuple[list[Mapping[str, Any]], str | None]:
    """Yield documented or daily-shaped rows; refuse month-only aggregates."""

    if isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, Mapping)]
        return rows, None
    if not isinstance(payload, Mapping):
        return [], "token-stats JSON must be an object or array."

    if isinstance(payload.get("observations"), list):
        return [], None

    entries = payload.get("entries")
    if isinstance(entries, list):
        return [item for item in entries if isinstance(item, Mapping)], None

    for key in ("daily", "days", "data"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, Mapping)], None

    monthly = payload.get("monthly")
    if isinstance(monthly, list):
        dated = []
        for item in monthly:
            if not isinstance(item, Mapping):
                continue
            if "date" in item or "day" in item or "timestamp" in item or "observed_at" in item:
                dated.append(item)
        if dated:
            return dated, None
        return [], "token-stats monthly rows lack a calendar day and cannot drive THIS/AVG."

    return [], "token-stats JSON did not match a documented dated schema."


def parse_token_stats_payload(
    payload: object,
    *,
    provider: str,
    account: str | None = None,
    location_hint: str = "token-stats",
    max_records: int = 20_000,
) -> CollectorAttempt:
    """Normalize token-stats JSON without guessing undated aggregates."""

    if isinstance(payload, Mapping) and payload.get("schema") == "quotadeck.collector.v1":
        return parse_quotadeck_import(
            payload,
            provider=provider,
            account=account,
            source_kind=UsageSourceKind.TOKEN_STATS,
            max_records=max_records,
        )

    rows, schema_error = _iter_candidate_rows(payload)
    if schema_error and not rows:
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.UNUSABLE,
            reason=schema_error,
        )
    if not rows:
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.UNUSABLE,
            reason="token-stats JSON contained no dated usage rows.",
        )

    observations: list[UsageObservation] = []
    malformed = 0
    truncated = False
    undated = 0
    for index, row in enumerate(rows):
        if len(observations) >= max_records:
            truncated = True
            break
        item = observation_from_row(
            row,
            provider=provider,
            source_kind=UsageSourceKind.TOKEN_STATS,
            account=account,
            index=index,
        )
        if item is None:
            if map_client_to_provider(row.get("client")) not in {None, provider}:
                continue
            if not any(key in row for key in ("date", "day", "timestamp", "observed_at", "createdAt")):
                undated += 1
            malformed += 1
            continue
        observations.append(item)

    if not observations:
        reason = (
            "token-stats rows had no calendar date or timestamp."
            if undated
            else "token-stats JSON had no usable dated token rows for this provider."
        )
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.UNUSABLE,
            reason=reason,
        )

    coverage = coverage_for(
        provider=provider,
        source_kind=UsageSourceKind.TOKEN_STATS,
        source_label="TOKEN-STATS",
        location_hint=location_hint,
        observations=tuple(observations),
        malformed=malformed,
        truncated=truncated,
        limitations=(
            "token-stats is an optional collector; prompt/response bodies are discarded.",
            "Reasoning tokens are a subset of output and are not added twice.",
        ),
    )
    return CollectorAttempt(
        CollectorName.TOKEN_STATS,
        CollectorStatus.OK,
        dataset=UsageDataset(tuple(observations), (coverage,)),
        truncated=truncated,
    )


def _lookback_window(today: date | None = None) -> tuple[date, date]:
    end = today or date.today()
    return end - timedelta(days=364), end


def collect_token_stats(
    provider: str,
    *,
    source_path: Path | str | None = None,
    account: str | None = None,
    settings: CollectorSettings | None = None,
    today: date | None = None,
    runner=None,
    payload: object | None = None,
) -> CollectorAttempt:
    """Prefer an explicit payload/import; otherwise try a bounded CLI call."""

    cfg = settings or CollectorSettings.from_env()
    if payload is not None:
        return parse_token_stats_payload(
            payload,
            provider=provider,
            account=account,
            max_records=cfg.max_records,
        )
    if cfg.import_path is not None:
        try:
            imported = json.loads(cfg.import_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return CollectorAttempt(
                CollectorName.TOKEN_STATS,
                CollectorStatus.UNUSABLE,
                reason="Collector import file could not be read as JSON.",
            )
        return parse_token_stats_payload(
            imported,
            provider=provider,
            account=account,
            location_hint=str(cfg.import_path),
            max_records=cfg.max_records,
        )

    if not cfg.enable_token_stats and cfg.token_stats_executable is None:
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.MISSING,
            reason="token-stats collection is disabled.",
        )
    executable = None
    if cfg.token_stats_executable is not None:
        executable = cfg.token_stats_executable
    elif cfg.enable_token_stats:
        executable = discover_token_stats(cfg)
    if executable is None:
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.MISSING,
            reason="token-stats is not installed.",
        )

    client = TOKEN_STATS_CLIENTS.get(provider)
    if client is None:
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.UNUSABLE,
            reason="This provider has no token-stats client mapping.",
        )

    since, until = _lookback_window(today)
    argv = [
        str(executable),
        "monthly",
        "--json",
        "--client",
        client,
        "--since",
        since.isoformat(),
        "--until",
        until.isoformat(),
    ]
    extras: dict[str, str] = {}
    if source_path is not None:
        root = Path(source_path).expanduser()
        if provider == "codex":
            extras["CODEX_HOME"] = str(root)
        elif provider == "claude":
            extras["CLAUDE_CONFIG_DIR"] = str(root)
        elif provider == "grok":
            extras["GROK_HOME"] = str(root)
    result = run_bounded_command(
        argv,
        timeout_seconds=cfg.timeout_seconds,
        max_output_bytes=cfg.max_json_bytes,
        env=minimal_child_environment(extras),
        runner=runner,
    )
    if result.issue_code:
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.UNUSABLE,
            reason=f"token-stats {result.issue_code.replace('_', ' ')}.",
        )
    if result.returncode not in {0, None}:
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.UNUSABLE,
            reason="token-stats exited unsuccessfully.",
        )
    try:
        decoded = decode_json_bytes(result.stdout, max_output_bytes=cfg.max_json_bytes)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return CollectorAttempt(
            CollectorName.TOKEN_STATS,
            CollectorStatus.UNUSABLE,
            reason="token-stats returned invalid JSON.",
        )
    return parse_token_stats_payload(
        decoded,
        provider=provider,
        account=account,
        location_hint=str(executable),
        max_records=cfg.max_records,
    )


__all__ = [
    "collect_token_stats",
    "discover_token_stats",
    "parse_token_stats_payload",
]
