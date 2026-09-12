"""Cursor cumulative usage from exported/server-derived collector data only.

``state.vscdb`` and CLI ``auth.json`` are login/quota sources, not a token
ledger. QuotaDeck never estimates Cursor tokens from those files. A Cursor
cumulative card is enabled only when a token-stats cache CSV or a documented
import supplies:

* calendar dates or timestamps
* mutually exclusive token categories
* account identity (column, ``usage.<account>.csv``, or an explicit
  generic-``usage.csv`` bind)

A generic ``usage.csv`` without an account column is the token-stats *active*
cache. It is never silently attached to every Cursor card; callers must use
``bind_generic_cursor_usage_csv``.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import time as _wall_clock

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
from quotadeck.usage.process import (
    minimal_child_environment,
    run_bounded_command,
)
from quotadeck.usage.token_stats import discover_token_stats, parse_token_stats_payload


_ACCOUNT_FILE = re.compile(r"^usage\.(?P<account>[^.]+)\.csv\Z", re.IGNORECASE)
_MAX_CSV_ROWS = 20_000
_MAX_CSV_BYTES = 8 * 1024 * 1024
_SYNC_MARKER = "usage.last-sync-attempt"
_CURSOR_SYNC_ARGV = ("cursor", "sync", "--json")

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

_LEGACY_LIMITATIONS = (
    "Cursor cumulative usage comes only from exported/server-derived collector data.",
    "state.vscdb and auth.json are never treated as a token ledger.",
)
_DASHBOARD_LIMITATIONS = _LEGACY_LIMITATIONS + (
    "Cursor usage export does not include reasoning tokens; they are unknown, not measured.",
    "Total Tokens is a vendor rollup and is not used as a token category.",
)

_Clock = Callable[[], float]
_INCLUDED_COST_LABELS = frozenset({"included", "-", "nan"})


@dataclass(frozen=True, slots=True)
class CursorReportedCost:
    """Vendor-reported CSV dollars. Never a LIST / API-equivalent cost."""

    usd: Decimal | None
    source: str = "cost"


@dataclass(frozen=True, slots=True)
class CursorUsageRow:
    """One attributable Cursor cache row plus optional reported cost."""

    observation: UsageObservation
    reported_cost: CursorReportedCost | None = None


@dataclass(frozen=True, slots=True)
class CursorCsvResult:
    """CSV parse result. ``attempt.dataset`` stays token-only."""

    attempt: CollectorAttempt
    rows: tuple[CursorUsageRow, ...] = ()

    @property
    def usable(self) -> bool:
        return self.attempt.usable


def parse_cursor_reported_cost(value: object) -> Decimal | None:
    """Parse Cost / Cost to you. Absent or unparseable stays None.

    token-stats treats ``Included``, ``-``, and NaN as 0.0; Kind
    ``Errored, No Charge`` uses Cost ``-``. Those proven labels become
    ``Decimal('0')``. Other non-numeric text stays ``None``.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.casefold()
    if lowered in _INCLUDED_COST_LABELS or lowered.startswith("errored"):
        return Decimal("0")
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        number = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def reported_cost_from_cursor_row(row: Mapping[str, str]) -> CursorReportedCost | None:
    by_compact = {
        _compact_header(key): value for key, value in row.items() if key
    }
    if not any(name in by_compact for name in ("costtoyou", "cost", "apicost")):
        return None
    to_you = by_compact.get("costtoyou")
    if to_you is not None and str(to_you).strip():
        return CursorReportedCost(
            parse_cursor_reported_cost(to_you),
            source="cost_to_you",
        )
    cost = by_compact.get("cost", by_compact.get("apicost"))
    if cost is None:
        return CursorReportedCost(None, source="cost")
    return CursorReportedCost(parse_cursor_reported_cost(cost), source="cost")


def _norm(name: str) -> str:
    text = name.strip()
    chars: list[str] = []
    for index, char in enumerate(text):
        if char.isupper() and index and text[index - 1].islower():
            chars.append("_")
        chars.append(char.casefold())
    return "".join(chars).replace("-", "_")


def _compact_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def account_from_cursor_filename(path: Path) -> str | None:
    match = _ACCOUNT_FILE.fullmatch(path.name)
    if match is None:
        return None
    account = match.group("account").strip()
    return account or None


def bind_generic_cursor_usage_csv(
    *,
    requested_account: str,
    bound_account: str | None,
) -> bool:
    """Return True only when a generic ``usage.csv`` may be attributed.

    token-stats writes the *active* account to ``usage.csv`` with no account
    column. That file may be bound to ``requested_account`` only when
    ``bound_account`` is an explicit, case-insensitive match. Callers that have
    more than one Cursor card must not pass a bound account (or must pass a
    distinct bound account per card). Matching is never inferred from
    credential files.
    """

    requested = requested_account.strip()
    bound = (bound_account or "").strip()
    if not requested or not bound:
        return False
    return requested.casefold() == bound.casefold()


def default_cursor_cache_dirs() -> tuple[Path, ...]:
    homes: list[Path] = []
    tokscale_home = os.environ.get("TOKSCALE_CONFIG_DIR", "").strip()
    if tokscale_home:
        homes.append(Path(tokscale_home).expanduser() / "cursor-cache")
    for env_name, parts in (
        ("XDG_CONFIG_HOME", ("tokscale", "cursor-cache")),
        ("APPDATA", ("tokscale", "cursor-cache")),
    ):
        raw = os.environ.get(env_name, "").strip()
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


def _is_skipped_cursor_cache_path(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts}
    if "archive" in parts:
        return True
    name = path.name.casefold()
    if name.startswith("."):
        return True
    if name.startswith("usage.backup"):
        return True
    if name == _SYNC_MARKER:
        return True
    return False


def discover_cursor_export_files(
    *,
    explicit: Path | None = None,
    account: str | None = None,
    bind_generic_account: str | None = None,
    cache_dirs: Sequence[Path] | None = None,
) -> list[Path]:
    if explicit is not None:
        path = explicit.expanduser()
        return [path] if path.is_file() else []
    requested = (account or "").strip()
    allow_generic = bind_generic_cursor_usage_csv(
        requested_account=requested,
        bound_account=bind_generic_account,
    )
    named: list[Path] = []
    json_files: list[Path] = []
    generic: list[Path] = []
    roots = tuple(cache_dirs) if cache_dirs is not None else default_cursor_cache_dirs()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_file() or _is_skipped_cursor_cache_path(child):
                continue
            suffix = child.suffix.casefold()
            if suffix == ".json":
                json_files.append(child)
                continue
            if suffix != ".csv":
                continue
            file_account = account_from_cursor_filename(child)
            if file_account:
                if requested and file_account.casefold() != requested.casefold():
                    continue
                named.append(child)
                continue
            if child.name.casefold() == "usage.csv":
                if allow_generic:
                    generic.append(child)
    return named + json_files + generic


def _row_account(row: Mapping[str, str], filename_account: str | None) -> str | None:
    compacted = {_compact_header(key): value for key, value in row.items()}
    lowered = {_norm(key): value for key, value in row.items()}
    for name in _ACCOUNT_COLUMNS:
        value = lowered.get(name) or compacted.get(_compact_header(name))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return filename_account


def _row_mapping(row: Mapping[str, str]) -> dict[str, str]:
    return {_norm(key): value.strip() for key, value in row.items() if value is not None}


def _token_int(value: object) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").strip()
    if not text or not text.lstrip("-").isdigit():
        return 0
    try:
        number = int(text)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def is_cursor_dashboard_csv(headers: Sequence[str] | None) -> bool:
    compacted = {_compact_header(name) for name in headers or () if name}
    has_without = bool(compacted & {"inputwocachewrite", "inputwithoutcachewrite"})
    has_with = bool(compacted & {"inputwcachewrite", "inputwithcachewrite"})
    return has_without or (has_with and "outputtokens" in compacted)


def _dashboard_field(row: Mapping[str, str], *aliases: str) -> str:
    by_compact = {_compact_header(key): (value or "").strip() for key, value in row.items() if key}
    for alias in aliases:
        value = by_compact.get(alias)
        if value:
            return value
    return ""


def normalize_cursor_csv_row(row: Mapping[str, str], *, dashboard: bool) -> dict[str, str]:
    """Map a CSV row onto collector keys without inventing reasoning or totals."""

    if not dashboard:
        return _row_mapping(row)

    date_value = _dashboard_field(row, "date")
    model = _dashboard_field(row, "model")
    without = _token_int(
        _dashboard_field(row, "inputwocachewrite", "inputwithoutcachewrite")
    )
    with_cache = _token_int(
        _dashboard_field(row, "inputwcachewrite", "inputwithcachewrite")
    )
    cache_read = _token_int(_dashboard_field(row, "cacheread"))
    output = _token_int(_dashboard_field(row, "outputtokens", "output"))
    mapped: dict[str, str] = {
        "model": model,
        "input": str(without),
        "output": str(output),
        "cacheRead": str(cache_read),
        "cacheWrite": str(max(0, with_cache - without)),
    }
    if date_value:
        mapped["date"] = date_value[:10] if len(date_value) >= 10 else date_value
        if "T" in date_value or date_value.endswith("Z"):
            mapped["observed_at"] = date_value
    account = _row_account(row, None)
    if account:
        mapped["account"] = account
    return mapped


def parse_cursor_csv(
    path: Path,
    *,
    account: str,
    max_records: int = _MAX_CSV_ROWS,
    max_bytes: int = _MAX_CSV_BYTES,
    bind_generic_account: str | None = None,
) -> CollectorAttempt:
    return parse_cursor_csv_result(
        path,
        account=account,
        max_records=max_records,
        max_bytes=max_bytes,
        bind_generic_account=bind_generic_account,
    ).attempt


def parse_cursor_csv_result(
    path: Path,
    *,
    account: str,
    max_records: int = _MAX_CSV_ROWS,
    max_bytes: int = _MAX_CSV_BYTES,
    bind_generic_account: str | None = None,
) -> CursorCsvResult:
    filename_account = account_from_cursor_filename(path)
    if (
        filename_account is None
        and path.name.casefold() == "usage.csv"
        and bind_generic_cursor_usage_csv(
            requested_account=account,
            bound_account=bind_generic_account,
        )
    ):
        filename_account = account.strip()
    try:
        size = path.stat().st_size
    except OSError:
        return CursorCsvResult(
            CollectorAttempt(
                CollectorName.CURSOR_EXPORT,
                CollectorStatus.UNUSABLE,
                reason="Cursor export file could not be read.",
            )
        )
    if size > max_bytes:
        return CursorCsvResult(
            CollectorAttempt(
                CollectorName.CURSOR_EXPORT,
                CollectorStatus.UNUSABLE,
                reason="Cursor export exceeded the bounded input size.",
            )
        )
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError:
        return CursorCsvResult(
            CollectorAttempt(
                CollectorName.CURSOR_EXPORT,
                CollectorStatus.UNUSABLE,
                reason="Cursor export file could not be read.",
            )
        )
    rows: list[CursorUsageRow] = []
    malformed = 0
    truncated = False
    saw_account = False
    dashboard = False
    with handle:
        try:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return CursorCsvResult(
                    CollectorAttempt(
                        CollectorName.CURSOR_EXPORT,
                        CollectorStatus.UNUSABLE,
                        reason="Cursor CSV is missing a header row.",
                    )
                )
            dashboard = is_cursor_dashboard_csv(reader.fieldnames)
            headers = {_norm(name) for name in reader.fieldnames if name}
            compacted = {_compact_header(name) for name in reader.fieldnames if name}
            has_date = bool(headers & set(_DATE_COLUMNS)) or "date" in compacted
            if not has_date:
                return CursorCsvResult(
                    CollectorAttempt(
                        CollectorName.CURSOR_EXPORT,
                        CollectorStatus.UNUSABLE,
                        reason="Cursor CSV has no date or timestamp column.",
                    )
                )
            for index, raw in enumerate(reader):
                if index >= max_records:
                    truncated = True
                    break
                mapped = normalize_cursor_csv_row(raw, dashboard=dashboard)
                row_account = _row_account(raw, filename_account) or mapped.get("account")
                if filename_account and not row_account:
                    row_account = filename_account
                if row_account:
                    saw_account = True
                    if row_account.casefold() != account.casefold():
                        continue
                elif filename_account is None:
                    continue
                if "total" in mapped or "total_tokens" in mapped:
                    mapped = {
                        key: value
                        for key, value in mapped.items()
                        if _compact_header(key) not in {"total", "totaltokens"}
                    }
                if tokens_from_mapping(mapped) is None:
                    malformed += 1
                    continue
                item = observation_from_row(
                    mapped,
                    provider="cursor",
                    source_kind=UsageSourceKind.CURSOR_EXPORT,
                    default_model="cursor",
                    account=account,
                    index=index,
                )
                if item is None:
                    malformed += 1
                    continue
                rows.append(
                    CursorUsageRow(
                        item,
                        reported_cost=reported_cost_from_cursor_row(raw),
                    )
                )
        except csv.Error:
            return CursorCsvResult(
                CollectorAttempt(
                    CollectorName.CURSOR_EXPORT,
                    CollectorStatus.UNUSABLE,
                    reason="Cursor CSV could not be parsed.",
                )
            )

    if not saw_account and filename_account is None:
        return CursorCsvResult(
            CollectorAttempt(
                CollectorName.CURSOR_EXPORT,
                CollectorStatus.UNUSABLE,
                reason=(
                    "Cursor export lacks account attribution. Bind generic usage.csv "
                    "with QUOTADECK_CURSOR_USAGE_CSV_ACCOUNT for exactly one account."
                ),
            )
        )
    observations = tuple(item.observation for item in rows)
    if not observations:
        return CursorCsvResult(
            CollectorAttempt(
                CollectorName.CURSOR_EXPORT,
                CollectorStatus.UNUSABLE,
                reason="Cursor export had no dated token rows for this account.",
            )
        )
    coverage = coverage_for(
        provider="cursor",
        source_kind=UsageSourceKind.CURSOR_EXPORT,
        source_label="CURSOR EXPORT",
        location_hint=str(path),
        observations=observations,
        malformed=malformed,
        truncated=truncated,
        limitations=_DASHBOARD_LIMITATIONS if dashboard else _LEGACY_LIMITATIONS,
    )
    return CursorCsvResult(
        CollectorAttempt(
            CollectorName.CURSOR_EXPORT,
            CollectorStatus.OK,
            dataset=UsageDataset(observations, (coverage,)),
            truncated=truncated,
        ),
        rows=tuple(rows),
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


def _iter_cache_usage_files(roots: Sequence[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = root.iterdir()
        except OSError:
            continue
        for child in children:
            if not child.is_file() or _is_skipped_cursor_cache_path(child):
                continue
            name = child.name.casefold()
            if name == "usage.csv" or _ACCOUNT_FILE.fullmatch(child.name):
                found.append(child)
            elif name == _SYNC_MARKER:
                found.append(child)
    return found


def cursor_cache_is_fresh(
    roots: Sequence[Path],
    *,
    ttl_seconds: float,
    clock: _Clock | None = None,
) -> bool:
    """True when a prior sync/cache write is still inside the refresh TTL."""

    if ttl_seconds < 0:
        return False
    now = (clock or _wall_clock)()
    latest: float | None = None
    for path in _iter_cache_usage_files(roots):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    if latest is None:
        return False
    return (now - latest) < ttl_seconds


def _touch_sync_marker(root: Path, *, now: float | None = None) -> None:
    try:
        if not root.is_dir():
            return
        marker = root / _SYNC_MARKER
        marker.write_bytes(b"")
        if now is not None:
            os.utime(marker, times=(now, now))
    except OSError:
        return


def maybe_sync_cursor_cache(
    settings: CollectorSettings,
    *,
    cache_dirs: Sequence[Path] | None = None,
    runner=None,
    clock: _Clock | None = None,
) -> None:
    """Best-effort ``token-stats cursor sync --json``; never reads credential files.

    Stdin and stderr are discarded by the bounded runner. Stdout is not parsed
    or retained. Timeout or failure leaves existing cache files in place.
    """

    if not settings.enable_cursor_sync:
        return
    executable = settings.token_stats_executable
    if executable is None:
        executable = discover_token_stats(settings)
    if executable is None:
        return
    roots = tuple(cache_dirs) if cache_dirs is not None else default_cursor_cache_dirs()
    now = (clock or _wall_clock)()
    if cursor_cache_is_fresh(
        roots,
        ttl_seconds=settings.cursor_sync_ttl_seconds,
        clock=lambda: now,
    ):
        return
    run_bounded_command(
        [str(executable), *_CURSOR_SYNC_ARGV],
        timeout_seconds=settings.timeout_seconds,
        max_output_bytes=settings.max_json_bytes,
        env=minimal_child_environment(),
        runner=runner,
    )
    for root in roots:
        _touch_sync_marker(root, now=now)


def collect_cursor_export(
    *,
    account: str,
    settings: CollectorSettings | None = None,
    payload: object | None = None,
    runner=None,
    clock: _Clock | None = None,
    cache_dirs: Sequence[Path] | None = None,
) -> CollectorAttempt:
    cfg = settings or CollectorSettings.from_env()
    if payload is not None:
        return parse_cursor_payload(
            payload, account=account, max_records=cfg.max_records
        )
    explicit = cfg.cursor_export_path or cfg.import_path
    if explicit is None:
        maybe_sync_cursor_cache(
            cfg,
            cache_dirs=cache_dirs,
            runner=runner,
            clock=clock,
        )
    files = discover_cursor_export_files(
        explicit=explicit,
        account=account,
        bind_generic_account=cfg.cursor_usage_csv_account,
        cache_dirs=cache_dirs,
    )
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
                bind_generic_account=cfg.cursor_usage_csv_account,
            )
        if attempt.usable:
            return attempt
        last = attempt
    return last


__all__ = [
    "CursorCsvResult",
    "CursorReportedCost",
    "CursorUsageRow",
    "account_from_cursor_filename",
    "bind_generic_cursor_usage_csv",
    "collect_cursor_export",
    "cursor_cache_is_fresh",
    "default_cursor_cache_dirs",
    "discover_cursor_export_files",
    "is_cursor_dashboard_csv",
    "maybe_sync_cursor_cache",
    "normalize_cursor_csv_row",
    "parse_cursor_csv",
    "parse_cursor_csv_result",
    "parse_cursor_payload",
    "parse_cursor_reported_cost",
    "reported_cost_from_cursor_row",
]
