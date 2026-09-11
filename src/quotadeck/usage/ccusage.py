"""Optional ccusage validation and fallback boundary.

ccusage supports Codex and Claude local logs (and other CLIs) with documented
daily JSON. It does not cover Cursor. QuotaDeck uses it only to:

* cross-check overlapping daily totals from token-stats or a native scan, or
* serve as a fallback when the preferred source is missing *and* the payload
  has calendar dates plus a single scoped provider.

Disagreeing totals are never blended. The primary observations stay as-is and
coverage becomes ``PARTIAL``. Missing account/period attribution is ``UNUSABLE``.
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
    ValidationResult,
    ValidationStatus,
    coverage_for,
    observation_from_row,
    tokens_from_mapping,
    validate_daily_totals,
)
from quotadeck.usage.models import UsageDataset, UsageObservation, UsageSourceKind
from quotadeck.usage.process import (
    decode_json_bytes,
    minimal_child_environment,
    resolve_optional_executable,
    run_bounded_command,
)


CCUSAGE_PROVIDERS = {"codex", "claude"}


def discover_ccusage(settings: CollectorSettings | None = None) -> Path | None:
    if settings and settings.ccusage_executable is not None:
        return settings.ccusage_executable
    return resolve_optional_executable("ccusage", env_var="QUOTADECK_CCUSAGE")


def _model_rows(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    breakdowns = row.get("modelBreakdowns")
    if isinstance(breakdowns, list) and breakdowns:
        models: list[Mapping[str, Any]] = []
        for item in breakdowns:
            if isinstance(item, Mapping):
                merged = dict(item)
                for key in ("date", "day", "period", "month"):
                    if key in row and key not in merged:
                        merged[key] = row[key]
                models.append(merged)
        if models:
            return models
    breakdown = row.get("breakdown")
    if isinstance(breakdown, Mapping) and breakdown:
        models = []
        for name, item in breakdown.items():
            if not isinstance(item, Mapping):
                continue
            merged = dict(item)
            merged.setdefault("model", name)
            for key in ("date", "day", "period", "month"):
                if key in row and key not in merged:
                    merged[key] = row[key]
            models.append(merged)
        if models:
            return models
    return [row]


def _daily_rows(payload: object) -> list[Mapping[str, Any]] | None:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return None
    for key in ("daily", "data"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, Mapping)]
    return None


def parse_ccusage_payload(
    payload: object,
    *,
    provider: str,
    location_hint: str = "ccusage",
    max_records: int = 20_000,
) -> CollectorAttempt:
    """Parse documented ccusage daily JSON. Month-only reports are refused."""

    if provider not in CCUSAGE_PROVIDERS:
        return CollectorAttempt(
            CollectorName.CCUSAGE,
            CollectorStatus.UNUSABLE,
            reason="ccusage is not a Cursor or Grok account-ledger source.",
        )
    rows = _daily_rows(payload)
    if rows is None:
        return CollectorAttempt(
            CollectorName.CCUSAGE,
            CollectorStatus.UNUSABLE,
            reason="ccusage JSON did not match the documented daily schema.",
        )

    observations: list[UsageObservation] = []
    malformed = 0
    truncated = False
    unclear = 0
    for row_index, row in enumerate(rows):
        agents = row.get("agents")
        if isinstance(agents, list) and len(agents) > 1:
            # A mixed multi-agent row cannot be attributed to one QuotaDeck account.
            unclear += 1
            continue
        if isinstance(agents, list) and agents:
            agent_row = agents[0]
            if not isinstance(agent_row, Mapping):
                malformed += 1
                continue
            agent_name = str(agent_row.get("agent") or "").casefold()
            if agent_name and agent_name != provider:
                continue
            row = {**row, **agent_row}
        for model_index, model_row in enumerate(_model_rows(row)):
            if len(observations) >= max_records:
                truncated = True
                break
            if tokens_from_mapping(model_row) is None:
                malformed += 1
                continue
            item = observation_from_row(
                model_row,
                provider=provider,
                source_kind=UsageSourceKind.CCUSAGE,
                index=row_index * 64 + model_index,
            )
            if item is None:
                malformed += 1
                continue
            observations.append(item)
        if truncated:
            break

    if not observations:
        reason = (
            "ccusage rows mixed multiple agents without a single account attribution."
            if unclear
            else "ccusage JSON had no dated token rows for this provider."
        )
        return CollectorAttempt(
            CollectorName.CCUSAGE,
            CollectorStatus.UNUSABLE,
            reason=reason,
        )
    coverage = coverage_for(
        provider=provider,
        source_kind=UsageSourceKind.CCUSAGE,
        source_label="CCUSAGE",
        location_hint=location_hint,
        observations=tuple(observations),
        malformed=malformed,
        truncated=truncated,
        limitations=(
            "ccusage is a validation/fallback boundary, not a blended source.",
            "Reasoning is a subset of output and is not added twice.",
        ),
    )
    return CollectorAttempt(
        CollectorName.CCUSAGE,
        CollectorStatus.OK,
        dataset=UsageDataset(tuple(observations), (coverage,)),
        truncated=truncated,
    )


def collect_ccusage(
    provider: str,
    *,
    source_path: Path | str | None = None,
    settings: CollectorSettings | None = None,
    today: date | None = None,
    runner=None,
    payload: object | None = None,
) -> CollectorAttempt:
    cfg = settings or CollectorSettings.from_env()
    if provider not in CCUSAGE_PROVIDERS:
        return CollectorAttempt(
            CollectorName.CCUSAGE,
            CollectorStatus.UNUSABLE,
            reason="ccusage cannot attribute Cursor or Grok account totals.",
        )
    if payload is not None:
        return parse_ccusage_payload(
            payload, provider=provider, max_records=cfg.max_records
        )

    executable = None
    if cfg.ccusage_executable is not None:
        executable = cfg.ccusage_executable
    elif cfg.enable_ccusage:
        executable = discover_ccusage(cfg)
    if executable is None:
        return CollectorAttempt(
            CollectorName.CCUSAGE,
            CollectorStatus.MISSING,
            reason="ccusage is not installed.",
        )
    since, until = (today or date.today()) - timedelta(days=364), today or date.today()
    argv = [
        str(executable),
        provider,
        "daily",
        "--json",
        "--since",
        since.strftime("%Y%m%d"),
        "--until",
        until.strftime("%Y%m%d"),
    ]
    extras: dict[str, str] = {}
    if source_path is not None:
        root = Path(source_path).expanduser()
        if provider == "codex":
            extras["CODEX_HOME"] = str(root)
        elif provider == "claude":
            extras["CLAUDE_CONFIG_DIR"] = str(root)
    result = run_bounded_command(
        argv,
        timeout_seconds=cfg.timeout_seconds,
        max_output_bytes=cfg.max_json_bytes,
        env=minimal_child_environment(extras),
        runner=runner,
    )
    if result.issue_code:
        return CollectorAttempt(
            CollectorName.CCUSAGE,
            CollectorStatus.UNUSABLE,
            reason=f"ccusage {result.issue_code.replace('_', ' ')}.",
        )
    if result.returncode not in {0, None}:
        return CollectorAttempt(
            CollectorName.CCUSAGE,
            CollectorStatus.UNUSABLE,
            reason="ccusage exited unsuccessfully.",
        )
    try:
        decoded = decode_json_bytes(result.stdout, max_output_bytes=cfg.max_json_bytes)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return CollectorAttempt(
            CollectorName.CCUSAGE,
            CollectorStatus.UNUSABLE,
            reason="ccusage returned invalid JSON.",
        )
    return parse_ccusage_payload(
        decoded,
        provider=provider,
        location_hint=str(executable),
        max_records=cfg.max_records,
    )


def validate_or_explain(
    primary: UsageDataset | None,
    secondary: CollectorAttempt,
) -> ValidationResult:
    if secondary.status is CollectorStatus.MISSING:
        return ValidationResult(ValidationStatus.SKIPPED, secondary.reason)
    if not secondary.usable or primary is None:
        return ValidationResult(
            ValidationStatus.UNUSABLE,
            secondary.reason or "ccusage result cannot be attributed.",
        )
    assert secondary.dataset is not None
    return validate_daily_totals(primary, secondary.dataset)


__all__ = [
    "CCUSAGE_PROVIDERS",
    "collect_ccusage",
    "discover_ccusage",
    "parse_ccusage_payload",
    "validate_or_explain",
]
