"""Conservative cumulative-usage adapters for Grok Build.

Grok exposes two materially different usage sources:

* ``grok usage <session-id>`` returns exact persisted *session* totals and
  recorded cost ticks.  Those totals are not an account ledger: resumed and
  forked sessions can include inherited history, and the command's documented
  turn rows have no event timestamp.  This module therefore never sums session
  totals and never turns ``updatedAt`` into a usage date.
* External OpenTelemetry v1 emits timestamped, per-model API-request events
  after an explicit opt-in.  A caller that owns those collector records can
  supply normalized events to :func:`build_grok_otel_dataset`.

The separation is deliberate.  It prevents a convenient-looking but incorrect
daily average or account total from being displayed as measured data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from quotadeck.discovery.executables import find_executable
from quotadeck.providers.grok.auth import grok_home
from quotadeck.usage.models import (
    ModelUsage,
    TokenUsage,
    UsageCoverage,
    UsageDataset,
    UsageObservation,
    UsageSourceKind,
)
from quotadeck.usage.normalized import (
    DISCOVERED_GROK_SESSION_LIMITATIONS,
    GROK_DISCOVERED_SESSION_SOURCE,
    GROK_PROVIDER_AGGREGATE_SOURCE,
    GROK_USAGE_SOURCE,
    NormalizedUsageRecord,
    UsageConfidence,
    aggregate_normalized_records,
    grok_web_unsupported_record,
)
from quotadeck.usage.pricing import estimate_api_equivalent_cost


GROK_COST_TICKS_PER_USD = 10_000_000_000
GROK_USAGE_CACHE_SCHEMA = "quotadeck.grok-usage.v1"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_SESSIONS = 256
DEFAULT_MAX_JSON_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_DISCOVERY_ENTRIES = 100_000
DEFAULT_MAX_CACHE_BYTES = 256 * 1024
MAX_GROK_USAGE_INTEGER = (1 << 63) - 1
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CACHE_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "prompt",
        "transcript",
        "message",
        "content",
    }
)


class GrokCapability(str, Enum):
    """Whether a source can answer a cumulative-usage question."""

    EXACT = "exact"
    CONDITIONAL = "conditional"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class GrokCumulativeSupport:
    """Stable support/limitation contract for the two official sources."""

    session_totals: GrokCapability = GrokCapability.EXACT
    account_total: GrokCapability = GrokCapability.UNAVAILABLE
    per_model: GrokCapability = GrokCapability.CONDITIONAL
    daily: GrokCapability = GrokCapability.CONDITIONAL
    one_year: GrokCapability = GrokCapability.CONDITIONAL
    recorded_cost: GrokCapability = GrokCapability.CONDITIONAL
    account_association: GrokCapability = GrokCapability.CONDITIONAL
    limitations: tuple[str, ...] = (
        "Local `grok usage` totals are per session, not an account-wide ledger.",
        "Resume/fork history can overlap, so session totals must not be summed.",
        "`updatedAt` is file metadata, not a per-turn usage timestamp.",
        "Daily/model history requires caller-supplied external OTel v1 records.",
        "External OTel is off by default and cannot recover pre-opt-in history.",
        "One-year coverage depends entirely on retained collector records.",
        "Local session directories are not proof of the currently signed-in account.",
        "OTel carries no cost metric; only persisted CLI cost ticks are exact.",
        "grok.com consumer Web Chat is not a Grok Build cumulative source.",
    )


GROK_CUMULATIVE_SUPPORT = GrokCumulativeSupport()


class GrokUsageFormatError(ValueError):
    """The official command returned JSON that violates its usage contract."""


def _non_negative_int(value: object, field_name: str, *, required: bool) -> int:
    if value is None and not required:
        return 0
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_GROK_USAGE_INTEGER
    ):
        raise GrokUsageFormatError(
            f"{field_name} must be a bounded non-negative integer"
        )
    return value


def _official_int(
    raw: Mapping[str, Any],
    name: str,
    *,
    required: bool = False,
) -> int:
    if name not in raw:
        if required:
            raise GrokUsageFormatError(f"missing {name}")
        return 0
    return _non_negative_int(raw[name], name, required=True)


def reported_cost_ticks(value: object) -> int | None:
    """Keep only strictly positive tick counts.  0/negative means unreported."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise GrokUsageFormatError("costUsdTicks must be a bounded integer")
    if value <= 0:
        return None
    if value > MAX_GROK_USAGE_INTEGER:
        raise GrokUsageFormatError("costUsdTicks must be a bounded non-negative integer")
    return value


def _optional_bool(raw: Mapping[str, Any], name: str) -> bool:
    value = raw.get(name)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise GrokUsageFormatError(f"{name} must be a boolean")
    return value


def _optional_model_id(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GrokUsageFormatError(f"{field_name} must be a non-empty string")
    return value.strip()


def _parse_rfc3339(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GrokUsageFormatError(f"{field_name} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GrokUsageFormatError(
            f"{field_name} must be an RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise GrokUsageFormatError(f"{field_name} must include a timezone")
    return parsed


def _optional_rfc3339(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_rfc3339(value, field_name)


@dataclass(frozen=True, slots=True)
class GrokUsageSummary:
    """One exact summary from the persisted Grok usage ledger.

    Grok's ``inputTokens`` includes cache reads/writes.  ``tokens`` converts it
    into QuotaDeck's mutually-exclusive token categories.
    """

    inclusive_input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    model_calls: int = 0
    turn_count: int = 0
    cost_usd_ticks: int | None = None
    cost_is_partial: bool = False
    usage_is_incomplete: bool = False
    primary_model_id: str | None = None

    @property
    def tokens(self) -> TokenUsage:
        uncached = (
            self.inclusive_input_tokens
            - self.cached_read_tokens
            - self.cache_creation_tokens
        )
        return TokenUsage(
            input_tokens=uncached,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_read_tokens,
            cache_write_tokens=self.cache_creation_tokens,
            reasoning_output_tokens=self.reasoning_tokens,
        )

    @property
    def cost_usd(self) -> Decimal | None:
        if (
            self.cost_usd_ticks is None
            or self.cost_is_partial
            or self.usage_is_incomplete
        ):
            return None
        return Decimal(self.cost_usd_ticks) / Decimal(GROK_COST_TICKS_PER_USD)


def _parse_summary(raw: object, field_name: str) -> GrokUsageSummary:
    if not isinstance(raw, Mapping):
        raise GrokUsageFormatError(f"{field_name} must be an object")
    input_tokens = _official_int(raw, "inputTokens", required=True)
    output_tokens = _official_int(raw, "outputTokens", required=True)
    total_tokens = _official_int(raw, "totalTokens", required=True)
    cached_read = _official_int(raw, "cachedReadTokens")
    cache_creation = _official_int(raw, "cacheCreationTokens")
    reasoning = _official_int(raw, "reasoningTokens")
    if cached_read + cache_creation > input_tokens:
        raise GrokUsageFormatError(
            f"{field_name} cache tokens exceed inclusive inputTokens"
        )
    if reasoning > output_tokens:
        raise GrokUsageFormatError(
            f"{field_name} reasoningTokens exceed outputTokens"
        )
    if total_tokens != input_tokens + output_tokens:
        raise GrokUsageFormatError(
            f"{field_name} totalTokens does not equal inputTokens + outputTokens"
        )
    cost_is_partial = _optional_bool(raw, "costIsPartial")
    usage_is_incomplete = _optional_bool(raw, "usageIsIncomplete")
    ticks = reported_cost_ticks(raw.get("costUsdTicks"))
    if cost_is_partial or usage_is_incomplete:
        ticks = None
    return GrokUsageSummary(
        inclusive_input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_read_tokens=cached_read,
        cache_creation_tokens=cache_creation,
        reasoning_tokens=reasoning,
        model_calls=_official_int(raw, "modelCalls"),
        turn_count=_official_int(raw, "turnCount"),
        cost_usd_ticks=ticks,
        cost_is_partial=cost_is_partial,
        usage_is_incomplete=usage_is_incomplete,
        primary_model_id=_optional_model_id(
            raw.get("primaryModelId"), f"{field_name}.primaryModelId"
        ),
    )


def _parse_model_usage(
    raw: Mapping[str, Any],
    field_name: str,
    *,
    expected: GrokUsageSummary,
) -> tuple[tuple[ModelUsage, ...], bool]:
    """Return model totals only when the complete map reconciles exactly."""

    value = raw.get("modelUsage")
    if value is None:
        return (), False
    if not isinstance(value, Mapping):
        return (), False
    parsed: list[ModelUsage] = []
    try:
        for model, summary_raw in value.items():
            if not isinstance(model, str) or not model.strip():
                raise GrokUsageFormatError(f"{field_name}.modelUsage has empty model")
            summary = _parse_summary(summary_raw, f"{field_name}.modelUsage[{model!r}]")
            parsed.append(ModelUsage("grok", model.strip(), summary.tokens))
    except GrokUsageFormatError:
        return (), False
    parsed.sort(key=lambda item: (-item.tokens.total_tokens, item.model))
    complete = TokenUsage.sum(item.tokens for item in parsed) == expected.tokens
    if not complete:
        return (), False
    return tuple(parsed), True


@dataclass(frozen=True, slots=True)
class GrokTurnUsage:
    """One command turn.  ``endedAt`` is the only official turn timestamp."""

    turn_number: int
    ended_at: datetime | None
    summary: GrokUsageSummary
    model_totals: tuple[ModelUsage, ...] = field(default_factory=tuple)
    per_model_complete: bool = False


@dataclass(frozen=True, slots=True)
class GrokSessionUsage:
    """Exact persisted total for one session, never an account total."""

    session_id: str
    updated_at: datetime
    summary: GrokUsageSummary
    turns: tuple[GrokTurnUsage, ...]
    model_totals: tuple[ModelUsage, ...] = field(default_factory=tuple)
    per_model_complete: bool = False
    turns_complete: bool = True

    @property
    def tokens(self) -> TokenUsage:
        return self.summary.tokens

    @property
    def cost_usd_ticks(self) -> int | None:
        return self.summary.cost_usd_ticks

    @property
    def cost_usd(self) -> Decimal | None:
        return self.summary.cost_usd


def parse_grok_usage_payload(
    payload: object, *, expected_session_id: str | None = None
) -> GrokSessionUsage:
    """Parse the official ``grok usage`` JSON envelope strictly."""

    if not isinstance(payload, Mapping):
        raise GrokUsageFormatError("usage payload must be an object")
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not session_id.strip():
        raise GrokUsageFormatError("sessionId must be a non-empty string")
    if expected_session_id is not None and session_id != expected_session_id:
        raise GrokUsageFormatError("sessionId did not match the requested session")
    updated_at = _parse_rfc3339(payload.get("updatedAt"), "updatedAt")
    session_raw = payload.get("session")
    summary = _parse_summary(session_raw, "session")
    assert isinstance(session_raw, Mapping)
    session_models, session_models_complete = _parse_model_usage(
        session_raw, "session", expected=summary
    )

    rows = payload.get("turns")
    if not isinstance(rows, list):
        raise GrokUsageFormatError("turns must be an array")
    turns: list[GrokTurnUsage] = []
    turns_complete = True
    seen_turn_numbers: set[int] = set()
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, Mapping):
                raise GrokUsageFormatError("turn row must be an object")
            turn_number = _official_int(row, "turnNumber", required=True)
            if turn_number in seen_turn_numbers:
                raise GrokUsageFormatError("duplicate turnNumber")
            turn_summary = _parse_summary(row, f"turns[{index}]")
            models, models_complete = _parse_model_usage(
                row,
                f"turns[{index}]",
                expected=turn_summary,
            )
            ended_at = _optional_rfc3339(row.get("endedAt"), f"turns[{index}].endedAt")
        except GrokUsageFormatError:
            turns_complete = False
            continue
        seen_turn_numbers.add(turn_number)
        turns.append(
            GrokTurnUsage(
                turn_number=turn_number,
                ended_at=ended_at,
                summary=turn_summary,
                model_totals=models,
                per_model_complete=models_complete,
            )
        )
    turns.sort(key=lambda item: item.turn_number)
    declared_turn_count = summary.turn_count if "turnCount" in session_raw else None
    if TokenUsage.sum(item.summary.tokens for item in turns) != summary.tokens or (
        declared_turn_count is not None and len(turns) != declared_turn_count
    ):
        turns_complete = False
    return GrokSessionUsage(
        session_id=session_id,
        updated_at=updated_at,
        summary=summary,
        turns=tuple(turns),
        model_totals=session_models,
        per_model_complete=session_models_complete,
        turns_complete=turns_complete,
    )


@dataclass(frozen=True, slots=True)
class GrokScanIssue:
    """Non-secret scanner failure code; command stderr is never retained."""

    code: str
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class GrokUsageScan:
    """Local session inventory plus an intentionally non-measured dataset.

    The dataset has no observations because assigning an entire session to its
    ``updatedAt`` day would fabricate a daily history.  Consumers may show the
    individual ``sessions`` table, but must not treat the labelled discovered
    inventory as an account ledger.
    """

    sessions: tuple[GrokSessionUsage, ...]
    dataset: UsageDataset
    issues: tuple[GrokScanIssue, ...] = field(default_factory=tuple)
    support: GrokCumulativeSupport = GROK_CUMULATIVE_SUPPORT
    session_records: tuple[NormalizedUsageRecord, ...] = field(default_factory=tuple)
    discovered_session_aggregate: NormalizedUsageRecord = field(
        default_factory=lambda: aggregate_normalized_records(
            (),
            provider="grok",
            source=GROK_DISCOVERED_SESSION_SOURCE,
            limitations=DISCOVERED_GROK_SESSION_LIMITATIONS,
        )
    )
    provider_aggregate: NormalizedUsageRecord = field(
        default_factory=lambda: aggregate_normalized_records(
            (),
            provider="grok",
            source=GROK_PROVIDER_AGGREGATE_SOURCE,
            limitations=DISCOVERED_GROK_SESSION_LIMITATIONS,
        )
    )
    web_unsupported: NormalizedUsageRecord = field(
        default_factory=grok_web_unsupported_record
    )
    cache_hits: int = 0

    @property
    def has_exact_session_totals(self) -> bool:
        return bool(self.sessions)

    @property
    def can_aggregate_account(self) -> bool:
        return False

    def session(self, session_id: str) -> GrokSessionUsage | None:
        return next(
            (item for item in self.sessions if item.session_id == session_id), None
        )


@dataclass(frozen=True, slots=True)
class _DiscoveredGrokSession:
    session_id: str
    directory: Path
    usage_mtime_ns: int = 0
    usage_size: int = 0


@dataclass(frozen=True, slots=True)
class _GrokSessionDiscovery:
    sessions: tuple[_DiscoveredGrokSession, ...] = ()
    scan_truncated: bool = False
    read_errors: int = 0
    invalid_session_ids: int = 0
    ambiguous_session_ids: int = 0

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(item.session_id for item in self.sessions)


def _usage_file_fingerprint(directory: Path) -> tuple[int, int]:
    usage = directory / "usage.json"
    try:
        if usage.is_file() and not usage.is_symlink():
            stat = usage.stat()
            return int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        pass
    return 0, 0


def _discover_grok_sessions(
    root: Path,
    *,
    max_sessions: int,
    max_entries: int,
) -> _GrokSessionDiscovery:
    """Discover bounded, option-safe IDs without reading conversation content."""

    sessions_root = root / "sessions"
    if not sessions_root.is_dir():
        return _GrokSessionDiscovery()
    try:
        resolved_root = sessions_root.resolve()
    except OSError:
        return _GrokSessionDiscovery(read_errors=1)

    found: dict[str, Path] = {}
    ambiguous: set[str] = set()
    errors = 0
    invalid = 0
    entries_seen = 0
    truncated = False

    def note_walk_error(_error: OSError) -> None:
        nonlocal errors
        errors += 1

    try:
        for directory, directories, filenames in os.walk(
            sessions_root,
            topdown=True,
            onerror=note_walk_error,
            followlinks=False,
        ):
            directories.sort()
            filenames.sort()
            entries_seen += len(directories) + len(filenames)
            if entries_seen > max_entries:
                truncated = True
                break
            if "summary.json" not in filenames:
                continue
            summary = Path(directory) / "summary.json"
            try:
                safe_file = (
                    summary.is_file()
                    and not summary.is_symlink()
                    and summary.resolve().is_relative_to(resolved_root)
                )
            except (OSError, RuntimeError):
                errors += 1
                continue
            if not safe_file:
                errors += 1
                continue
            session_id = summary.parent.name
            if _SAFE_SESSION_ID.fullmatch(session_id) is None:
                invalid += 1
                continue
            previous = found.get(session_id)
            if previous is not None and previous != summary.parent:
                ambiguous.add(session_id)
                continue
            found[session_id] = summary.parent
            unique_count = sum(1 for key in found if key not in ambiguous)
            if unique_count > max_sessions:
                truncated = True
                break
    except (OSError, RuntimeError):
        errors += 1
    discovered: list[_DiscoveredGrokSession] = []
    for session_id in sorted(found):
        if session_id in ambiguous:
            continue
        directory = found[session_id]
        mtime_ns, size = _usage_file_fingerprint(directory)
        discovered.append(
            _DiscoveredGrokSession(session_id, directory, mtime_ns, size)
        )
        if len(discovered) >= max_sessions:
            if len(found) - len(ambiguous) > max_sessions:
                truncated = True
            break
    return _GrokSessionDiscovery(
        tuple(discovered),
        scan_truncated=truncated,
        read_errors=errors,
        invalid_session_ids=invalid,
        ambiguous_session_ids=len(ambiguous),
    )


def discover_grok_session_ids(root: Path | None = None) -> tuple[str, ...]:
    """Discover a bounded set of retained, option-safe session IDs."""

    home = root or grok_home()
    return _discover_grok_sessions(
        home,
        max_sessions=DEFAULT_MAX_SESSIONS,
        max_entries=DEFAULT_MAX_DISCOVERY_ENTRIES,
    ).session_ids


_Runner = Callable[..., subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class _BoundedCommandResult:
    returncode: int | None = None
    stdout: bytes = b""
    issue_code: str | None = None


def _minimal_child_environment(home: Path) -> dict[str, str]:
    """Pass only process-launch essentials; never forward unrelated secrets."""

    child = {"GROK_HOME": str(home)}
    for name in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
    ):
        value = os.environ.get(name)
        if isinstance(value, str) and value:
            child[name] = value
    return child


def _run_bounded_command(
    argv: list[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
    env: Mapping[str, str],
) -> _BoundedCommandResult:
    """Drain stdout incrementally and terminate as soon as the cap is crossed."""

    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=dict(env),
        shell=False,
        bufsize=0,
    )
    assert process.stdout is not None
    output = bytearray()
    oversized = threading.Event()
    read_failed = threading.Event()

    def drain_stdout() -> None:
        try:
            while chunk := process.stdout.read(64 * 1024):
                remaining = max_output_bytes + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > max_output_bytes or len(chunk) > remaining:
                    oversized.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    break
        except OSError:
            read_failed.set()
        finally:
            try:
                process.stdout.close()
            except OSError:
                pass

    reader = threading.Thread(target=drain_stdout, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        reader.join(timeout=1.0)
        return _BoundedCommandResult(issue_code="command_timeout")
    reader.join(timeout=1.0)
    if reader.is_alive() or read_failed.is_set():
        try:
            process.kill()
        except OSError:
            pass
        return _BoundedCommandResult(issue_code="command_failed")
    if oversized.is_set():
        return _BoundedCommandResult(issue_code="output_too_large")
    return _BoundedCommandResult(returncode=returncode, stdout=bytes(output))


def _run_injected_command(
    runner: _Runner,
    argv: list[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
    env: Mapping[str, str],
) -> _BoundedCommandResult:
    """Compatibility seam for deterministic tests; production uses Popen above."""

    completed = runner(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=False,
        check=False,
        timeout=timeout_seconds,
        env=dict(env),
        stdin=subprocess.DEVNULL,
    )
    raw_stdout = completed.stdout or b""
    stdout = (
        raw_stdout.encode("utf-8", errors="replace")
        if isinstance(raw_stdout, str)
        else bytes(raw_stdout)
    )
    if len(stdout) > max_output_bytes:
        return _BoundedCommandResult(issue_code="output_too_large")
    return _BoundedCommandResult(returncode=completed.returncode, stdout=stdout)


def default_grok_usage_cache_dir() -> Path:
    """Return the credential-free Grok usage cache directory."""

    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "QuotaDeck" / "usage-cache" / "grok"


def grok_usage_cache_path(cache_dir: Path, home: Path, session_id: str) -> Path:
    try:
        resolved = str(home.expanduser().resolve())
    except OSError:
        resolved = str(home)
    digest = hashlib.sha256(f"{resolved}\0{session_id}".encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def _home_digest(home: Path) -> str:
    try:
        resolved = str(home.expanduser().resolve())
    except OSError:
        resolved = str(home)
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()


def _cache_has_secrets(payload: object) -> bool:
    if isinstance(payload, Mapping):
        for key, item in payload.items():
            name = str(key).strip().casefold().replace("-", "_")
            if name in _CACHE_SECRET_KEYS:
                return True
            if _cache_has_secrets(item):
                return True
        return False
    if isinstance(payload, list):
        return any(_cache_has_secrets(item) for item in payload)
    return False


def _summary_to_official_dict(summary: GrokUsageSummary) -> dict[str, object]:
    payload: dict[str, object] = {
        "inputTokens": summary.inclusive_input_tokens,
        "outputTokens": summary.output_tokens,
        "totalTokens": summary.total_tokens,
        "cachedReadTokens": summary.cached_read_tokens,
        "cacheCreationTokens": summary.cache_creation_tokens,
        "reasoningTokens": summary.reasoning_tokens,
        "modelCalls": summary.model_calls,
        "turnCount": summary.turn_count,
    }
    if summary.cost_usd_ticks is not None:
        payload["costUsdTicks"] = summary.cost_usd_ticks
    if summary.cost_is_partial:
        payload["costIsPartial"] = True
    if summary.usage_is_incomplete:
        payload["usageIsIncomplete"] = True
    if summary.primary_model_id:
        payload["primaryModelId"] = summary.primary_model_id
    return payload


def _session_cache_payload(
    session: GrokSessionUsage,
    *,
    home_digest: str,
    usage_mtime_ns: int,
    usage_size: int,
) -> dict[str, object]:
    session_raw = _summary_to_official_dict(session.summary)
    if session.per_model_complete and session.model_totals:
        session_raw["modelUsage"] = {
            item.model: {
                "inputTokens": (
                    item.tokens.input_tokens
                    + item.tokens.cached_input_tokens
                    + item.tokens.cache_write_tokens
                ),
                "outputTokens": item.tokens.output_tokens,
                "totalTokens": item.tokens.total_tokens,
                "cachedReadTokens": item.tokens.cached_input_tokens,
                "cacheCreationTokens": item.tokens.cache_write_tokens,
                "reasoningTokens": item.tokens.reasoning_output_tokens,
            }
            for item in session.model_totals
        }
    turns: list[dict[str, object]] = []
    for turn in session.turns:
        row = _summary_to_official_dict(turn.summary)
        row["turnNumber"] = turn.turn_number
        if turn.ended_at is not None:
            row["endedAt"] = turn.ended_at.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        turns.append(row)
    return {
        "schema": GROK_USAGE_CACHE_SCHEMA,
        "home_sha256": home_digest,
        "session_id": session.session_id,
        "usage_mtime_ns": usage_mtime_ns,
        "usage_size": usage_size,
        "envelope": {
            "sessionId": session.session_id,
            "updatedAt": session.updated_at.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "session": session_raw,
            "turns": turns,
        },
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError:
        return
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def read_grok_usage_cache(
    path: Path,
    *,
    home: Path,
    session_id: str,
    usage_mtime_ns: int,
    usage_size: int,
) -> GrokSessionUsage | None:
    """Load cached usage metadata.  Corrupt or secret-bearing files are ignored."""

    try:
        if not path.is_file() or path.stat().st_size > DEFAULT_MAX_CACHE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, Mapping) or _cache_has_secrets(payload):
        return None
    if str(payload.get("schema") or "") != GROK_USAGE_CACHE_SCHEMA:
        return None
    if (
        payload.get("home_sha256") != _home_digest(home)
        or payload.get("session_id") != session_id
        or payload.get("usage_mtime_ns") != usage_mtime_ns
        or payload.get("usage_size") != usage_size
    ):
        return None
    try:
        return parse_grok_usage_payload(
            payload.get("envelope"), expected_session_id=session_id
        )
    except (GrokUsageFormatError, ValueError, RecursionError):
        return None


def write_grok_usage_cache(
    path: Path,
    session: GrokSessionUsage,
    *,
    home: Path,
    usage_mtime_ns: int,
    usage_size: int,
) -> None:
    payload = _session_cache_payload(
        session,
        home_digest=_home_digest(home),
        usage_mtime_ns=usage_mtime_ns,
        usage_size=usage_size,
    )
    if _cache_has_secrets(payload):
        return
    _atomic_write_json(path, payload)


def _api_equivalent_usd(session: GrokSessionUsage) -> Decimal | None:
    if session.summary.usage_is_incomplete:
        return None
    if session.per_model_complete and session.model_totals:
        total = Decimal("0")
        for item in session.model_totals:
            estimate = estimate_api_equivalent_cost(
                item.provider, item.model, item.tokens
            )
            if estimate.usd is None:
                return None
            total += estimate.usd
        return total
    model = session.summary.primary_model_id
    if not model:
        return None
    return estimate_api_equivalent_cost("grok", model, session.tokens).usd


def grok_session_record(
    session: GrokSessionUsage,
    *,
    usd_krw_rate: Decimal | None = None,
    account: str | None = None,
) -> NormalizedUsageRecord:
    """Per-session record.  ``updatedAt`` is never copied into ``timestamp``."""

    tokens = session.tokens
    api_usd = _api_equivalent_usd(session)
    krw = (
        api_usd * usd_krw_rate
        if api_usd is not None and usd_krw_rate is not None and usd_krw_rate > 0
        else None
    )
    limitations = [
        "Local `grok usage` totals are per session, not an account-wide ledger.",
        "updatedAt is file metadata, not a usage-event timestamp.",
    ]
    if session.summary.usage_is_incomplete:
        limitations.append("Official usageIsIncomplete is set; totals may grow.")
    if session.summary.cost_is_partial:
        limitations.append("Official costIsPartial is set; reported cost is unknown.")
    if session.cost_usd is None:
        limitations.append("Unreported or partial cost ticks are not treated as free.")
    confidence = (
        UsageConfidence.PARTIAL
        if session.summary.usage_is_incomplete or not session.turns_complete
        else UsageConfidence.EXACT
    )
    return NormalizedUsageRecord(
        provider="grok",
        account=account,
        model=session.summary.primary_model_id,
        timestamp=None,
        session=session.session_id,
        turn=None,
        input_tokens=tokens.input_tokens,
        output_tokens=tokens.output_tokens,
        cache_read_tokens=tokens.cached_input_tokens,
        cache_write_tokens=tokens.cache_write_tokens,
        reasoning_tokens=tokens.reasoning_output_tokens,
        total_tokens=tokens.total_tokens,
        reported_cost_usd=session.cost_usd,
        api_equivalent_cost_usd=api_usd,
        api_equivalent_cost_krw=krw,
        source=GROK_USAGE_SOURCE,
        confidence=confidence,
        limitations=tuple(limitations),
    )


def grok_turn_record(
    session: GrokSessionUsage,
    turn: GrokTurnUsage,
    *,
    usd_krw_rate: Decimal | None = None,
    account: str | None = None,
) -> NormalizedUsageRecord:
    """Turn row using official ``endedAt``, never session ``updatedAt``."""

    tokens = turn.summary.tokens
    api_usd = None
    if not turn.summary.usage_is_incomplete and turn.summary.primary_model_id:
        api_usd = estimate_api_equivalent_cost(
            "grok", turn.summary.primary_model_id, tokens
        ).usd
    krw = (
        api_usd * usd_krw_rate
        if api_usd is not None and usd_krw_rate is not None and usd_krw_rate > 0
        else None
    )
    confidence = (
        UsageConfidence.PARTIAL
        if turn.summary.usage_is_incomplete
        else UsageConfidence.EXACT
    )
    return NormalizedUsageRecord(
        provider="grok",
        account=account,
        model=turn.summary.primary_model_id or session.summary.primary_model_id,
        timestamp=turn.ended_at,
        session=session.session_id,
        turn=turn.turn_number,
        input_tokens=tokens.input_tokens,
        output_tokens=tokens.output_tokens,
        cache_read_tokens=tokens.cached_input_tokens,
        cache_write_tokens=tokens.cache_write_tokens,
        reasoning_tokens=tokens.reasoning_output_tokens,
        total_tokens=tokens.total_tokens,
        reported_cost_usd=turn.summary.cost_usd,
        api_equivalent_cost_usd=api_usd,
        api_equivalent_cost_krw=krw,
        source=GROK_USAGE_SOURCE,
        confidence=confidence,
        limitations=(
            "Turn rows are session fragments, not an account ledger.",
            "endedAt is the official turn timestamp; updatedAt is not used.",
        ),
    )


def grok_session_to_records(
    session: GrokSessionUsage,
    *,
    usd_krw_rate: Decimal | None = None,
    account: str | None = None,
    include_turns: bool = False,
) -> tuple[NormalizedUsageRecord, ...]:
    records = [grok_session_record(session, usd_krw_rate=usd_krw_rate, account=account)]
    if include_turns:
        records.extend(
            grok_turn_record(
                session, turn, usd_krw_rate=usd_krw_rate, account=account
            )
            for turn in session.turns
        )
    return tuple(records)


def scan_grok_session_usage(
    root: Path | None = None,
    *,
    executable: str | Path | None = None,
    runner: _Runner | None = None,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    max_json_bytes: int = DEFAULT_MAX_JSON_BYTES,
    max_discovery_entries: int = DEFAULT_MAX_DISCOVERY_ENTRIES,
    now: datetime | None = None,
    cache_dir: Path | None = None,
    usd_krw_rate: Decimal | None = None,
    account: str | None = None,
) -> GrokUsageScan:
    """Run the official local usage command once per discovered session.

    Arguments are passed without a shell.  Output/stderr are never included in
    returned issues, which prevents an unexpected CLI diagnostic from leaking a
    credential into UI logs.  Unchanged ``usage.json`` fingerprints are served
    from a credential-free on-disk cache of normalized usage metadata.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_sessions < 1:
        raise ValueError("max_sessions must be at least 1")
    if max_json_bytes < 1:
        raise ValueError("max_json_bytes must be at least 1")
    if max_discovery_entries < 1:
        raise ValueError("max_discovery_entries must be at least 1")
    home = root or grok_home()
    resolved_cache = (
        cache_dir if cache_dir is not None else default_grok_usage_cache_dir()
    )
    discovery = _discover_grok_sessions(
        home,
        max_sessions=max_sessions,
        max_entries=max_discovery_entries,
    )
    discovered = discovery.session_ids
    issues: list[GrokScanIssue] = []
    if discovery.scan_truncated:
        issues.append(GrokScanIssue("session_limit_reached"))
    if discovery.read_errors:
        issues.append(GrokScanIssue("discovery_error"))
    if discovery.invalid_session_ids:
        issues.append(GrokScanIssue("invalid_session_id"))
    if discovery.ambiguous_session_ids:
        issues.append(GrokScanIssue("ambiguous_session_id"))

    resolved_executable: str | None
    if executable is None:
        located = find_executable("grok")
        resolved_executable = str(located) if located is not None else None
    else:
        resolved_executable = str(executable)
    parsed_sessions: list[GrokSessionUsage] = []
    cache_hits = 0

    if discovered and resolved_executable is None:
        issues.append(GrokScanIssue("command_not_found"))
    elif resolved_executable is not None:
        child_env = _minimal_child_environment(home)
        for item in discovery.sessions:
            session_id = item.session_id
            cache_path = grok_usage_cache_path(resolved_cache, home, session_id)
            cached = read_grok_usage_cache(
                cache_path,
                home=home,
                session_id=session_id,
                usage_mtime_ns=item.usage_mtime_ns,
                usage_size=item.usage_size,
            )
            if cached is not None:
                parsed_sessions.append(cached)
                cache_hits += 1
                if not cached.turns_complete:
                    issues.append(GrokScanIssue("partial_turn_rows", session_id))
                if not cached.per_model_complete:
                    issues.append(GrokScanIssue("per_model_unavailable", session_id))
                continue
            try:
                argv = [resolved_executable, "usage", session_id]
                completed = (
                    _run_bounded_command(
                        argv,
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=max_json_bytes,
                        env=child_env,
                    )
                    if runner is None
                    else _run_injected_command(
                        runner,
                        argv,
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=max_json_bytes,
                        env=child_env,
                    )
                )
            except subprocess.TimeoutExpired:
                issues.append(GrokScanIssue("command_timeout", session_id))
                continue
            except (OSError, ValueError):
                issues.append(GrokScanIssue("command_failed", session_id))
                continue
            if completed.issue_code is not None:
                issues.append(GrokScanIssue(completed.issue_code, session_id))
                continue
            if completed.returncode != 0:
                issues.append(GrokScanIssue("command_failed", session_id))
                continue
            try:
                payload = json.loads(completed.stdout.decode("utf-8"))
                parsed = parse_grok_usage_payload(
                    payload, expected_session_id=session_id
                )
            except (ValueError, RecursionError):
                issues.append(GrokScanIssue("invalid_output", session_id))
                continue
            parsed_sessions.append(parsed)
            write_grok_usage_cache(
                cache_path,
                parsed,
                home=home,
                usage_mtime_ns=item.usage_mtime_ns,
                usage_size=item.usage_size,
            )
            if not parsed.turns_complete:
                issues.append(GrokScanIssue("partial_turn_rows", session_id))
            if not parsed.per_model_complete:
                issues.append(GrokScanIssue("per_model_unavailable", session_id))

    scanned_at = now or datetime.now(timezone.utc)
    if scanned_at.tzinfo is None:
        scanned_at = scanned_at.replace(tzinfo=timezone.utc)
    limitations = list(GROK_CUMULATIVE_SUPPORT.limitations)
    if issues:
        limitations.append("Some discovered sessions could not be read completely.")
    coverage = UsageCoverage(
        provider="grok",
        source_kind=UsageSourceKind.LOCAL_OBSERVED,
        source_label="GROK USAGE (SESSION ONLY)",
        location_hint=str(home / "sessions"),
        scanned_at=scanned_at,
        files_discovered=(
            len(discovered)
            + discovery.invalid_session_ids
            + discovery.ambiguous_session_ids
            + (1 if discovery.scan_truncated else 0)
        ),
        files_read=len(parsed_sessions),
        read_errors=sum(
            issue.code
            in {
                "command_not_found",
                "command_timeout",
                "command_failed",
                "output_too_large",
                "invalid_output",
                "discovery_error",
            }
            for issue in issues
        ),
        usage_events_seen=sum(len(item.turns) for item in parsed_sessions),
        observations_emitted=0,
        malformed_usage_events=sum(
            issue.code in {
                "invalid_output",
                "partial_turn_rows",
                "invalid_session_id",
                "ambiguous_session_id",
            }
            for issue in issues
        ),
        scan_truncated=discovery.scan_truncated,
        limitations=tuple(limitations),
    )
    session_records = tuple(
        grok_session_record(item, usd_krw_rate=usd_krw_rate, account=account)
        for item in parsed_sessions
    )
    discovered_aggregate = aggregate_normalized_records(
        session_records,
        provider="grok",
        source=GROK_DISCOVERED_SESSION_SOURCE,
        limitations=DISCOVERED_GROK_SESSION_LIMITATIONS,
        account=account,
    )
    provider_aggregate = aggregate_normalized_records(
        session_records,
        provider="grok",
        source=GROK_PROVIDER_AGGREGATE_SOURCE,
        limitations=DISCOVERED_GROK_SESSION_LIMITATIONS,
        account=account,
    )
    return GrokUsageScan(
        sessions=tuple(parsed_sessions),
        dataset=UsageDataset(observations=(), coverages=(coverage,)),
        issues=tuple(issues),
        session_records=session_records,
        discovered_session_aggregate=discovered_aggregate,
        provider_aggregate=provider_aggregate,
        cache_hits=cache_hits,
    )


@dataclass(frozen=True, slots=True)
class GrokOtelApiEvent:
    """Normalized external OTel v1 ``grok_code.api_request`` event.

    A collector adapter must supply the real record timestamp and identity.  No
    local session metadata is substituted for either value.
    """

    observed_at: datetime
    user_id: str
    session_id: str
    event_sequence: int
    model: str
    inclusive_input_tokens: int
    output_tokens: int
    cached_read_tokens: int = 0
    reasoning_tokens: int = 0
    schema_version: str = "v1"

    def __post_init__(self) -> None:
        if self.schema_version != "v1":
            raise ValueError("only Grok external OTel schema v1 is supported")
        if self.observed_at.tzinfo is None:
            raise ValueError("OTel event timestamp must include a timezone")
        for name in ("user_id", "session_id", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        for name in (
            "event_sequence",
            "inclusive_input_tokens",
            "output_tokens",
            "cached_read_tokens",
            "reasoning_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.cached_read_tokens > self.inclusive_input_tokens:
            raise ValueError("cached_read_tokens cannot exceed input tokens")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens cannot exceed output tokens")

    @property
    def tokens(self) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.inclusive_input_tokens - self.cached_read_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_read_tokens,
            reasoning_output_tokens=self.reasoning_tokens,
        )

    def to_observation(self) -> UsageObservation:
        # UsageDataset.merge de-duplicates by provider/event_id rather than by
        # session.  Sequence numbers are only session-local, so include a
        # stable, non-reversible session discriminator in the event ID.
        session_digest = hashlib.sha256(self.session_id.encode("utf-8")).hexdigest()[:24]
        return UsageObservation(
            provider="grok",
            model=self.model,
            observed_at=self.observed_at,
            tokens=self.tokens,
            session_id=self.session_id,
            event_id=f"otel-v1:{session_digest}:{self.event_sequence}",
            source_kind=UsageSourceKind.EXTERNAL_OTEL,
        )


def build_grok_otel_dataset(
    events: Iterable[GrokOtelApiEvent],
    *,
    expected_user_id: str,
    source_label: str = "GROK EXTERNAL OTEL V1",
    location_hint: str = "EXTERNAL COLLECTOR",
    now: datetime | None = None,
) -> UsageDataset:
    """Build timestamped usage for one explicitly selected OTel identity.

    Requiring ``expected_user_id`` prevents a collector export containing
    several users from silently becoming one account total.
    """

    if not isinstance(expected_user_id, str) or not expected_user_id.strip():
        raise ValueError("expected_user_id must be a non-empty string")
    selected: list[UsageObservation] = []
    seen: dict[tuple[str, str], UsageObservation] = {}
    duplicates = 0
    event_count = 0
    for event in events:
        event_count += 1
        if event.user_id != expected_user_id:
            raise ValueError("OTel event user_id did not match expected_user_id")
        observation = event.to_observation()
        key = (observation.session_id, observation.event_id)
        previous = seen.get(key)
        if previous is not None:
            if previous != observation:
                raise ValueError("conflicting OTel event sequence in one session")
            duplicates += 1
            continue
        seen[key] = observation
        selected.append(observation)
    selected.sort(key=lambda item: (item.observed_at, item.session_id, item.event_id))
    scanned_at = now or datetime.now(timezone.utc)
    if scanned_at.tzinfo is None:
        scanned_at = scanned_at.replace(tzinfo=timezone.utc)
    observation_start = selected[0].observed_at if selected else None
    observation_end = selected[-1].observed_at if selected else None
    coverage = UsageCoverage(
        provider="grok",
        source_kind=UsageSourceKind.EXTERNAL_OTEL,
        source_label=source_label,
        location_hint=location_hint,
        scanned_at=scanned_at,
        files_discovered=1 if event_count else 0,
        files_read=1 if event_count else 0,
        usage_events_seen=event_count,
        observations_emitted=len(selected),
        duplicate_events_removed=duplicates,
        observation_start=observation_start,
        observation_end=observation_end,
        limitations=(
            "Coverage starts when external OTel export and collector retention start.",
            "OTel has no cost metric; join token events to a separate price snapshot.",
        ),
    )
    return UsageDataset(tuple(selected), (coverage,))


@dataclass(frozen=True, slots=True)
class GrokExternalOtelEnvironmentHint:
    """A non-authoritative view of environment opt-in, never fleet status."""

    master_requested: bool
    metrics_exporter: str | None

    @property
    def metrics_requested(self) -> bool:
        return self.master_requested and self.metrics_exporter in {"otlp", "console"}


def grok_external_otel_environment_hint(
    env: Mapping[str, str] | None = None,
) -> GrokExternalOtelEnvironmentHint:
    """Inspect opt-in names without reading endpoints, headers, or credentials."""

    values = os.environ if env is None else env
    master = str(values.get("GROK_EXTERNAL_OTEL", "")).strip().lower()
    requested = master in {"1", "true", "yes", "on"}
    exporter = str(values.get("OTEL_METRICS_EXPORTER", "none")).strip().lower()
    if exporter not in {"otlp", "console"}:
        exporter = None
    return GrokExternalOtelEnvironmentHint(requested, exporter)
