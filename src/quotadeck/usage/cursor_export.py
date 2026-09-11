"""Cursor cumulative usage from exported/server-derived collector data only.

``state.vscdb`` and CLI ``auth.json`` are login/quota sources, not a token
ledger. QuotaDeck never estimates Cursor tokens from those files. A Cursor
cumulative card is enabled only when a token-stats cache CSV or a documented
import supplies:

* calendar dates or timestamps
* mutually exclusive token categories
* account identity (column or ``usage.<account>.csv``)

A generic ``usage.csv`` without an account column is refused.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping
from pathlib import Path

from quotadeck.usage.collectors import (
    CollectorAttempt,
    CollectorName,
    CollectorSettings,
    CollectorStatus,
    coverage_for,
    observation_from_row,
    parse_quotadeck_import,
    tokens_from_mapping,
)
from quotadeck.usage.models import UsageDataset, UsageObservation, UsageSourceKind
from quotadeck.usage.token_stats import parse_token_stats_payload


_ACCOUNT_FILE = re.compile(r"^usage\.(?P<account>[^.]+)\.csv\Z", re.IGNORECASE)
_MAX_CSV_ROWS = 20_000
_MAX_CSV_BYTES = 8 * 1024 * 1024

_DATE_COLUMNS = ("date", "day", "timestamp", "observed_at", "created_at", "createdat")
_ACCOUNT_COLUMNS = (
    "account",
    "account_id",
    "accountid",
    "email",
    "user",
    "userid",
    "user_id",
)


def _norm(name: str) -> str:
    text = name.strip()
    chars: list[str] = []
    for index, char in enumerate(text):
        if char.isupper() and index and text[index - 1].islower():
            chars.append("_")
        chars.append(char.casefold())
    return "".join(chars).replace("-", "_")


def account_from_cursor_filename(path: Path) -> str | None:
    match = _ACCOUNT_FILE.fullmatch(path.name)
    if match is None:
        return None
    account = match.group("account").strip()
    return account or None


def default_cursor_cache_dirs() -> tuple[Path, ...]:
    homes: list[Path] = []
    for env_name, parts in (
        ("XDG_CONFIG_HOME", ("tokscale", "cursor-cache")),
        ("APPDATA", ("tokscale", "cursor-cache")),
    ):
        raw = __import__("os").environ.get(env_name, "").strip()
        if raw:
            homes.append(Path(raw).expanduser().joinpath(*parts))
    homes.append(Path.home() / ".config" / "tokscale" / "cursor-cache")
    unique: list[Path] = []
    seen: set[str] = set()
    for item in homes:
        key = str(item)
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return tuple(unique)


def discover_cursor_export_files(
    *,
    explicit: Path | None = None,
    account: str | None = None,
) -> list[Path]:
    if explicit is not None:
        path = explicit.expanduser()
        return [path] if path.is_file() else []
    found: list[Path] = []
    for root in default_cursor_cache_dirs():
        if not root.is_dir():
            continue
        try:
            for child in sorted(root.iterdir()):
                if not child.is_file():
                    continue
                if child.suffix.casefold() not in {".csv", ".json"}:
                    continue
                file_account = account_from_cursor_filename(child)
                if account and file_account and file_account.casefold() != account.casefold():
                    continue
                if child.name.casefold() == "usage.csv" or file_account or child.suffix.casefold() == ".json":
                    found.append(child)
        except OSError:
            continue
    return found


def _row_account(row: Mapping[str, str], filename_account: str | None) -> str | None:
    lowered = {_norm(key): value for key, value in row.items()}
    for name in _ACCOUNT_COLUMNS:
        value = lowered.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return filename_account


def _row_mapping(row: Mapping[str, str]) -> dict[str, str]:
    return {_norm(key): value.strip() for key, value in row.items() if value is not None}


def parse_cursor_csv(
    path: Path,
    *,
    account: str,
    max_records: int = _MAX_CSV_ROWS,
    max_bytes: int = _MAX_CSV_BYTES,
) -> CollectorAttempt:
    filename_account = account_from_cursor_filename(path)
    try:
        size = path.stat().st_size
    except OSError:
        return CollectorAttempt(
            CollectorName.CURSOR_EXPORT,
            CollectorStatus.UNUSABLE,
            reason="Cursor export file could not be read.",
        )
    if size > max_bytes:
        return CollectorAttempt(
            CollectorName.CURSOR_EXPORT,
            CollectorStatus.UNUSABLE,
            reason="Cursor export exceeded the bounded input size.",
        )
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError:
        return CollectorAttempt(
            CollectorName.CURSOR_EXPORT,
            CollectorStatus.UNUSABLE,
            reason="Cursor export file could not be read.",
        )
    observations: list[UsageObservation] = []
    malformed = 0
    truncated = False
    saw_account = False
    with handle:
        try:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return CollectorAttempt(
                    CollectorName.CURSOR_EXPORT,
                    CollectorStatus.UNUSABLE,
                    reason="Cursor CSV is missing a header row.",
                )
            headers = {_norm(name) for name in reader.fieldnames if name}
            if not (headers & set(_DATE_COLUMNS)):
                return CollectorAttempt(
                    CollectorName.CURSOR_EXPORT,
                    CollectorStatus.UNUSABLE,
                    reason="Cursor CSV has no date or timestamp column.",
                )
            for index, raw in enumerate(reader):
                if index >= max_records:
                    truncated = True
                    break
                row = _row_mapping(raw)
                row_account = _row_account(row, filename_account)
                if row_account:
                    saw_account = True
                    if row_account.casefold() != account.casefold():
                        continue
                elif filename_account is None:
                    # Generic usage.csv without per-row account identity.
                    continue
                if tokens_from_mapping(row) is None:
                    malformed += 1
                    continue
                item = observation_from_row(
                    row,
                    provider="cursor",
                    source_kind=UsageSourceKind.CURSOR_EXPORT,
                    default_model="cursor",
                    account=account,
                    index=index,
                )
                if item is None:
                    malformed += 1
                    continue
                observations.append(item)
        except csv.Error:
            return CollectorAttempt(
                CollectorName.CURSOR_EXPORT,
                CollectorStatus.UNUSABLE,
                reason="Cursor CSV could not be parsed.",
            )

    if not saw_account and filename_account is None:
        return CollectorAttempt(
            CollectorName.CURSOR_EXPORT,
            CollectorStatus.UNUSABLE,
            reason="Cursor export lacks account attribution.",
        )
    if not observations:
        return CollectorAttempt(
            CollectorName.CURSOR_EXPORT,
            CollectorStatus.UNUSABLE,
            reason="Cursor export had no dated token rows for this account.",
        )
    coverage = coverage_for(
        provider="cursor",
        source_kind=UsageSourceKind.CURSOR_EXPORT,
        source_label="CURSOR EXPORT",
        location_hint=str(path),
        observations=tuple(observations),
        malformed=malformed,
        truncated=truncated,
        limitations=(
            "Cursor cumulative usage comes only from exported/server-derived collector data.",
            "state.vscdb and auth.json are never treated as a token ledger.",
        ),
    )
    return CollectorAttempt(
        CollectorName.CURSOR_EXPORT,
        CollectorStatus.OK,
        dataset=UsageDataset(tuple(observations), (coverage,)),
        truncated=truncated,
    )


def parse_cursor_payload(
    payload: object,
    *,
    account: str,
    location_hint: str = "cursor-export",
    max_records: int = _MAX_CSV_ROWS,
) -> CollectorAttempt:
    if isinstance(payload, Mapping) and payload.get("schema") == "quotadeck.collector.v1":
        return parse_quotadeck_import(
            payload,
            provider="cursor",
            account=account,
            source_kind=UsageSourceKind.CURSOR_EXPORT,
            max_records=max_records,
        )
    attempt = parse_token_stats_payload(
        payload,
        provider="cursor",
        account=account,
        location_hint=location_hint,
        max_records=max_records,
    )
    if attempt.usable:
        return attempt
    return CollectorAttempt(
        CollectorName.CURSOR_EXPORT,
        CollectorStatus.UNUSABLE,
        reason=attempt.reason or "Cursor collector JSON was not attributable.",
    )


def collect_cursor_export(
    *,
    account: str,
    settings: CollectorSettings | None = None,
    payload: object | None = None,
) -> CollectorAttempt:
    cfg = settings or CollectorSettings.from_env()
    if payload is not None:
        return parse_cursor_payload(
            payload, account=account, max_records=cfg.max_records
        )
    explicit = cfg.cursor_export_path or cfg.import_path
    files = discover_cursor_export_files(explicit=explicit, account=account)
    if not files:
        return CollectorAttempt(
            CollectorName.CURSOR_EXPORT,
            CollectorStatus.MISSING,
            reason="No attributable Cursor usage export was found.",
        )
    last = CollectorAttempt(
        CollectorName.CURSOR_EXPORT,
        CollectorStatus.UNUSABLE,
        reason="No attributable Cursor usage export was found.",
    )
    for path in files:
        if path.suffix.casefold() == ".json":
            try:
                decoded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                last = CollectorAttempt(
                    CollectorName.CURSOR_EXPORT,
                    CollectorStatus.UNUSABLE,
                    reason="Cursor export JSON could not be read.",
                )
                continue
            attempt = parse_cursor_payload(
                decoded,
                account=account,
                location_hint=str(path),
                max_records=cfg.max_records,
            )
        else:
            attempt = parse_cursor_csv(
                path,
                account=account,
                max_records=cfg.max_records,
                max_bytes=cfg.max_json_bytes,
            )
        if attempt.usable:
            return attempt
        last = attempt
    return last


__all__ = [
    "account_from_cursor_filename",
    "collect_cursor_export",
    "default_cursor_cache_dirs",
    "discover_cursor_export_files",
    "parse_cursor_csv",
    "parse_cursor_payload",
]
