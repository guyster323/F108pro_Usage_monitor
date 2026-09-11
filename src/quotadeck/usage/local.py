"""Privacy-preserving scanners for locally retained CLI token metadata.

Only timestamps, model names, stable event identifiers, and token counters are
retained.  Prompt and response content is deliberately ignored.  Local files
are an observation ledger for this profile, not an authoritative account bill.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from quotadeck.core.mask import safe_display_text
from quotadeck.usage.models import (
    TokenUsage,
    UsageCoverage,
    UsageDataset,
    UsageObservation,
    UsageSourceKind,
)


_MAX_JSONL_LINE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_JSONL_BYTES = 512 * 1024 * 1024
_MAX_RECORDS_PER_SCAN = 1_000_000
_MAX_FILES_PER_SCAN = 20_000
_MAX_DIRECTORIES_PER_SCAN = 50_000
_MAX_DIRECTORY_ENTRIES_PER_SCAN = 250_000
_MAX_COUNTER_DIGITS = 128
_MAX_COUNTER_VALUE = (10**_MAX_COUNTER_DIGITS) - 1
_MAX_SAFE_FLOAT_INTEGER = (2**53) - 1
_scandir = os.scandir


@dataclass(slots=True)
class _ScanStats:
    provider: str
    source_label: str
    location_hint: str
    scanned_at: datetime
    files_discovered: int = 0
    files_read: int = 0
    read_errors: int = 0
    usage_events_seen: int = 0
    malformed_usage_events: int = 0
    duplicate_events_removed: int = 0
    counter_resets_seen: int = 0
    bytes_read: int = 0
    records_examined: int = 0
    scan_truncated: bool = False
    reading_stopped: bool = False
    observation_start: datetime | None = None
    observation_end: datetime | None = None
    limitations: list[str] = field(default_factory=list)

    def note_limit(self, detail: str, *, stop_reading: bool = False) -> None:
        self.scan_truncated = True
        self.reading_stopped = self.reading_stopped or stop_reading
        message = (
            f"Local history scan stopped at the {detail} safety limit; totals are partial."
        )
        if message not in self.limitations:
            self.limitations.append(message)

    def note_omission(self, detail: str) -> None:
        self.scan_truncated = True
        message = (
            f"Skipped {detail} while containing the scan to its local root; totals are partial."
        )
        if message not in self.limitations:
            self.limitations.append(message)

    def note_observation(self, observation: UsageObservation) -> None:
        moment = observation.observed_at
        if self.observation_start is None or moment < self.observation_start:
            self.observation_start = moment
        if self.observation_end is None or moment > self.observation_end:
            self.observation_end = moment

    def coverage(self, observation_count: int) -> UsageCoverage:
        limitations = list(self.limitations)
        if self.read_errors or self.malformed_usage_events:
            limitations.append(
                "Some local records could not be read or normalized; totals are partial."
            )
        if not observation_count:
            limitations.append(
                "No valid token observations were found; this is unknown, not measured zero."
            )
        return UsageCoverage(
            provider=self.provider,
            source_kind=UsageSourceKind.LOCAL_OBSERVED,
            source_label=self.source_label,
            location_hint=self.location_hint,
            scanned_at=self.scanned_at,
            files_discovered=self.files_discovered,
            files_read=self.files_read,
            read_errors=self.read_errors,
            usage_events_seen=self.usage_events_seen,
            observations_emitted=observation_count,
            malformed_usage_events=self.malformed_usage_events,
            duplicate_events_removed=self.duplicate_events_removed,
            counter_resets_seen=self.counter_resets_seen,
            bytes_read=self.bytes_read,
            records_examined=self.records_examined,
            scan_truncated=self.scan_truncated,
            observation_start=self.observation_start,
            observation_end=self.observation_end,
            limitations=tuple(dict.fromkeys(limitations)),
        )


@dataclass(frozen=True, slots=True)
class _CodexFileStream:
    """One independently decoded Codex cumulative stream.

    Files that claim the same session cannot be unioned merely because one
    cumulative snapshot happens to overlap. Keeping the stream boundary until
    prefix compatibility has been proved prevents divergent live/archive
    copies from inflating the total.
    """

    session_id: str
    path: Path
    observations: tuple[UsageObservation, ...]
    terminal: TokenUsage

    @property
    def score(self) -> tuple[int, int, int, str]:
        return (
            sum(
                observation.tokens.total_tokens
                for observation in self.observations
            ),
            self.terminal.total_tokens,
            len(self.observations),
            str(self.path).casefold(),
        )


def _codex_stream_is_prefix(
    candidate: _CodexFileStream,
    complete: _CodexFileStream,
) -> bool:
    """Return true only when ``candidate`` is an exact stream prefix."""

    if len(candidate.observations) > len(complete.observations):
        return False
    return all(
        left == right
        for left, right in zip(candidate.observations, complete.observations)
    )


def _codex_streams_are_prefix_compatible(
    left: _CodexFileStream,
    right: _CodexFileStream,
) -> bool:
    return _codex_stream_is_prefix(left, right) or _codex_stream_is_prefix(
        right, left
    )


def _path_hint(path: Path) -> str:
    try:
        home = Path.home().resolve()
        resolved = path.expanduser().resolve()
        if resolved == home:
            return "~"
        if resolved.is_relative_to(home):
            return str(Path("~") / resolved.relative_to(home))
        return str(resolved)
    except (OSError, RuntimeError):
        return str(path.expanduser())


def _safe_jsonl_files(
    roots: Iterable[Path], stats: _ScanStats
) -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[str] = set()
    directories_seen = 0
    entries_seen = 0
    for root in roots:
        try:
            expanded = root.expanduser()
            if expanded.is_symlink():
                stats.note_omission("a symbolic-link history root")
                continue
            root_stat = expanded.stat()
            if not stat.S_ISDIR(root_stat.st_mode):
                stats.note_omission("a non-directory history root")
                continue
            resolved_root = expanded.resolve(strict=True)
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError):
            stats.read_errors += 1
            continue

        pending = [resolved_root]
        while pending:
            if directories_seen >= _MAX_DIRECTORIES_PER_SCAN:
                stats.note_limit("directory-count")
                return tuple(
                    sorted(paths, key=lambda item: str(item).casefold(), reverse=True)
                )
            directory = pending.pop()
            directories_seen += 1
            child_directories: list[Path] = []
            try:
                with _scandir(directory) as entries:
                    for entry in entries:
                        if entries_seen >= _MAX_DIRECTORY_ENTRIES_PER_SCAN:
                            stats.note_limit("directory-entry-count")
                            return tuple(
                                sorted(
                                    paths,
                                    key=lambda item: str(item).casefold(),
                                    reverse=True,
                                )
                            )
                        entries_seen += 1
                        try:
                            if entry.is_symlink():
                                stats.note_omission("a symbolic-link history entry")
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                child_directories.append(Path(entry.path))
                                continue
                            if not entry.name.endswith(".jsonl") or not entry.is_file(
                                follow_symlinks=False
                            ):
                                continue
                            resolved = Path(entry.path).resolve(strict=True)
                            if not resolved.is_relative_to(resolved_root):
                                stats.note_omission("an out-of-root history entry")
                                continue
                            key = str(resolved)
                            if key in seen:
                                continue
                            if len(paths) >= _MAX_FILES_PER_SCAN:
                                stats.note_limit("file-count")
                                return tuple(
                                    sorted(
                                        paths,
                                        key=lambda item: str(item).casefold(),
                                        reverse=True,
                                    )
                                )
                            seen.add(key)
                            paths.append(resolved)
                        except (OSError, RuntimeError):
                            stats.read_errors += 1
            except OSError:
                stats.read_errors += 1
                continue
            # A LIFO walk with ascending insertion examines lexically newer
            # dated Codex directories first. The final sort does the same for
            # files if the byte/record budget is reached later.
            child_directories.sort(key=lambda item: str(item).casefold())
            pending.extend(child_directories)
    return tuple(sorted(paths, key=lambda item: str(item).casefold(), reverse=True))


def _records(path: Path, stats: _ScanStats) -> Iterator[dict[str, Any]]:
    """Yield JSON objects while turning damaged records into partial coverage."""

    try:
        if path.is_symlink():
            stats.read_errors += 1
            return
        with path.open("rb") as stream:
            stats.files_read += 1
            while True:
                remaining = _MAX_TOTAL_JSONL_BYTES - stats.bytes_read
                read_limit = min(_MAX_JSONL_LINE_BYTES + 1, remaining + 1)
                raw = stream.readline(max(1, read_limit))
                if not raw:
                    break
                if len(raw) > remaining:
                    stats.bytes_read += remaining
                    stats.note_limit("total-byte", stop_reading=True)
                    return
                stats.bytes_read += len(raw)
                if len(raw) > _MAX_JSONL_LINE_BYTES:
                    # Drain one oversized logical line without retaining it.
                    while raw and not raw.endswith(b"\n"):
                        remaining = _MAX_TOTAL_JSONL_BYTES - stats.bytes_read
                        read_limit = min(_MAX_JSONL_LINE_BYTES + 1, remaining + 1)
                        raw = stream.readline(max(1, read_limit))
                        if len(raw) > remaining:
                            stats.bytes_read += remaining
                            stats.note_limit("total-byte", stop_reading=True)
                            return
                        stats.bytes_read += len(raw)
                    stats.malformed_usage_events += 1
                    continue
                if not raw.strip():
                    continue
                if stats.records_examined >= _MAX_RECORDS_PER_SCAN:
                    stats.note_limit("record-count", stop_reading=True)
                    return
                stats.records_examined += 1
                try:
                    value = json.loads(raw)
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    RecursionError,
                    ValueError,
                ):
                    stats.malformed_usage_events += 1
                    continue
                if isinstance(value, dict):
                    yield value
                else:
                    stats.malformed_usage_events += 1
    except OSError:
        stats.read_errors += 1


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _first(mapping: Mapping[str, Any], *names: str) -> object:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _counter(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= _MAX_COUNTER_VALUE else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            return None
        converted = int(value)
        return converted if converted <= _MAX_SAFE_FLOAT_INTEGER else None
    if isinstance(value, str):
        text = value.strip()
        if (
            len(text) <= _MAX_COUNTER_DIGITS
            and text.isascii()
            and text.isdigit()
        ):
            try:
                return int(text)
            except (OverflowError, ValueError):
                return None
    return None


def _present_counter(
    mapping: Mapping[str, Any], *names: str
) -> tuple[bool, int | None]:
    for name in names:
        if name in mapping:
            return True, _counter(mapping[name])
    return False, 0


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _stable_id(*parts: object) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode(
        "utf-8", errors="surrogatepass"
    )
    return hashlib.sha256(encoded).hexdigest()[:32]


def _token_identity(tokens: TokenUsage) -> tuple[int, ...]:
    return (
        tokens.input_tokens,
        tokens.unclassified_input_tokens,
        tokens.output_tokens,
        tokens.cached_input_tokens,
        tokens.cache_write_tokens,
        tokens.cache_write_5m_tokens,
        tokens.cache_write_1h_tokens,
        tokens.reasoning_output_tokens,
    )


def _model_name(value: object) -> str:
    return safe_display_text(value, max_length=160)


def _identifier(value: object, *, max_length: int = 512) -> str | None:
    if not isinstance(value, str) or len(value) > max_length:
        return None
    cleaned = value.strip()
    return cleaned or None


def _codex_tokens(raw: Mapping[str, Any]) -> TokenUsage | None:
    input_present, raw_input = _present_counter(raw, "input_tokens", "inputTokens")
    cached_present, cached = _present_counter(
        raw, "cached_input_tokens", "cachedInputTokens"
    )
    cache_write_present, cache_write = _present_counter(
        raw,
        "cache_write_input_tokens",
        "cacheWriteInputTokens",
        "cache_creation_input_tokens",
        "cacheCreationInputTokens",
    )
    output_present, output = _present_counter(raw, "output_tokens", "outputTokens")
    reasoning_present, reasoning = _present_counter(
        raw, "reasoning_output_tokens", "reasoningOutputTokens"
    )
    total_present, reported_total = _present_counter(
        raw, "total_tokens", "totalTokens"
    )
    if not (
        input_present
        or cached_present
        or cache_write_present
        or output_present
        or reasoning_present
        or total_present
    ):
        return None
    if any(
        value is None
        for value in (
            raw_input,
            cached,
            cache_write,
            output,
            reasoning,
            reported_total,
        )
    ):
        return None
    assert raw_input is not None and cached is not None
    assert cache_write is not None and output is not None and reasoning is not None
    assert reported_total is not None
    # Codex reports cache reads and writes as mutually-exclusive subsets of
    # input_tokens. Its TUI calls both categories "input", but pricing requires
    # them to remain separate here.
    if (
        cached + cache_write > raw_input
        or reasoning > output
        or (total_present and reported_total != raw_input + output)
    ):
        return None
    return TokenUsage(
        input_tokens=raw_input - cached - cache_write,
        cached_input_tokens=cached,
        cache_write_tokens=cache_write,
        output_tokens=output,
        reasoning_output_tokens=reasoning,
    )


def _codex_roots(home: Path) -> tuple[Path, ...]:
    expanded = home.expanduser()
    if expanded.name in {"sessions", "archived_sessions"}:
        return (expanded,)
    return (expanded / "sessions", expanded / "archived_sessions")


def scan_codex_usage(
    home: Path | str,
    *,
    scanned_at: datetime | None = None,
) -> UsageDataset:
    """Scan Codex ``sessions`` and ``archived_sessions`` cumulative counters.

    Token-count records are cumulative within a session.  This scanner emits
    only monotonic increments, counts a decrease as a counter reset, and
    deduplicates a session copied between the live and archived directories.
    """

    root = Path(home).expanduser()
    now = scanned_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stats = _ScanStats(
        provider="codex",
        source_label="LOCAL CODEX HISTORY",
        location_hint=_path_hint(root),
        scanned_at=now,
        limitations=[
            "Totals cover retained history on this profile/device, not an account invoice.",
            "History retained across sign-ins cannot be separated by account.",
        ],
    )
    files = _safe_jsonl_files(_codex_roots(root), stats)
    stats.files_discovered = len(files)
    streams_by_session: dict[str, list[_CodexFileStream]] = {}

    for path in files:
        if stats.reading_stopped:
            break
        session_id = path.stem
        current_model = "unknown"
        previous_total: TokenUsage | None = None
        pre_cumulative_usage = TokenUsage()
        file_observations: dict[str, UsageObservation] = {}
        file_event_order: list[str] = []
        for record in _records(path, stats):
            payload = _mapping(record.get("payload")) or {}
            record_type = record.get("type")
            payload_type = payload.get("type")
            if record_type == "session_meta":
                value = _first(payload, "id", "session_id", "sessionId")
                normalized = _identifier(value, max_length=200)
                if normalized is not None:
                    session_id = normalized
                elif value is not None:
                    stats.malformed_usage_events += 1
                continue
            if record_type == "turn_context":
                candidate = _first(payload, "model", "model_name", "modelName")
                if candidate is not None:
                    current_model = _model_name(candidate)
                continue
            if payload_type == "thread_settings_applied":
                settings = _mapping(payload.get("thread_settings")) or {}
                candidate = _first(settings, "model", "model_name", "modelName")
                if candidate is not None:
                    current_model = _model_name(candidate)
                continue
            if payload_type == "model_reroute":
                candidate = _first(payload, "to_model", "toModel")
                if candidate is not None:
                    current_model = _model_name(candidate)
                continue

            token_usage_record = record_type == "token_usage_record"
            legacy_token_count = (
                record_type == "token_count"
                or payload_type == "token_count"
                or (record_type == "event_msg" and payload_type == "token_count")
            )
            if not token_usage_record and not legacy_token_count:
                continue

            if token_usage_record:
                info = payload
                total_raw = _mapping(
                    _first(payload, "thread_token_usage", "threadTokenUsage")
                )
                last_raw = _mapping(_first(payload, "usage"))
                if total_raw is None or last_raw is None:
                    # An available cumulative counter still yields a
                    # conservative subtotal; last-only official records are
                    # skipped below because overlap cannot be ruled out.
                    stats.malformed_usage_events += 1
                value = _first(payload, "session_id", "sessionId")
                normalized = _identifier(value, max_length=200)
                if normalized is not None:
                    session_id = normalized
                elif value is not None:
                    stats.malformed_usage_events += 1
            else:
                info = _mapping(payload.get("info")) or _mapping(record.get("info"))
                if info is None:
                    # Codex can emit a rate-limit-only token event before usage
                    # is available. It is not malformed token data.
                    continue
                total_raw = _mapping(
                    _first(info, "total_token_usage", "totalTokenUsage")
                )
                last_raw = _mapping(
                    _first(info, "last_token_usage", "lastTokenUsage")
                )
            if total_raw is None and last_raw is None:
                continue
            stats.usage_events_seen += 1
            moment = _timestamp(
                _first(record, "timestamp", "created_at", "createdAt")
                or _first(payload, "timestamp", "created_at", "createdAt")
            )
            if moment is None:
                stats.malformed_usage_events += 1
                continue
            model = _model_name(
                _first(info, "model", "model_name", "modelName")
                or _first(payload, "model", "model_name", "modelName")
                or current_model
            )

            tokens_for_identity: TokenUsage
            if total_raw is not None:
                current_total = _codex_tokens(total_raw)
                if current_total is None:
                    stats.malformed_usage_events += 1
                    continue
                if previous_total is None:
                    if pre_cumulative_usage.is_zero:
                        increment = current_total
                    else:
                        increment = current_total.delta_from(pre_cumulative_usage)
                        if increment is None:
                            # The last-only stream cannot be reconciled with
                            # this first cumulative value. Keep the already
                            # observed subtotal and start the cumulative cursor
                            # here without counting an ambiguous overlap.
                            stats.malformed_usage_events += 1
                            increment = TokenUsage()
                else:
                    increment = current_total.delta_from(previous_total)
                    if increment is None:
                        stats.counter_resets_seen += 1
                        increment = current_total
                previous_total = current_total
                tokens_for_identity = current_total
            else:
                assert last_raw is not None
                if token_usage_record or previous_total is not None:
                    # The official token_usage_record requires a cumulative
                    # thread counter. A legacy last-only notification can also
                    # mirror a response already represented by the established
                    # cumulative stream, so neither is safe to add here.
                    if previous_total is not None and not token_usage_record:
                        stats.malformed_usage_events += 1
                    continue
                increment = _codex_tokens(last_raw)
                if increment is None:
                    stats.malformed_usage_events += 1
                    continue
                tokens_for_identity = increment
            if increment.is_zero:
                continue

            raw_explicit_id = _first(
                payload,
                "response_id",
                "responseId",
                "id",
                "event_id",
                "eventId",
                "uuid",
            ) or _first(record, "id", "event_id", "eventId", "uuid")
            explicit_id = _identifier(raw_explicit_id)
            if raw_explicit_id is not None and explicit_id is None:
                stats.malformed_usage_events += 1
            if token_usage_record and explicit_id is None:
                stats.malformed_usage_events += 1
            if explicit_id is not None:
                # Response/event IDs are the strongest cross-file identity;
                # model/session metadata is checked below instead of weakening
                # deduplication by embedding container-specific values here.
                event_id = _stable_id("codex", "explicit", explicit_id)
            else:
                event_id = _stable_id(
                    "codex",
                    "session-fallback",
                    session_id,
                    moment.isoformat(),
                    _token_identity(tokens_for_identity),
                )
            observation = UsageObservation(
                provider="codex",
                model=model,
                observed_at=moment,
                tokens=increment,
                session_id=session_id,
                event_id=event_id,
            )
            previous = file_observations.get(event_id)
            if previous is not None:
                stats.duplicate_events_removed += 1
                compatible = (
                    previous.model == observation.model
                    and previous.observed_at == observation.observed_at
                    and previous.tokens == observation.tokens
                )
                if not compatible:
                    # Truncated/rotated copies can produce a different delta
                    # for the same cumulative snapshot. Never retain both.
                    # The smaller increment is the conservative reconciliation,
                    # while partial coverage prevents cost/ratio certification.
                    stats.malformed_usage_events += 1
                    if (
                        observation.tokens.total_tokens
                        < previous.tokens.total_tokens
                    ):
                        file_observations[event_id] = observation
                continue
            file_observations[event_id] = observation
            file_event_order.append(event_id)
            if total_raw is None:
                pre_cumulative_usage = pre_cumulative_usage + increment
        if file_event_order:
            stream = _CodexFileStream(
                session_id=session_id,
                path=path,
                observations=tuple(
                    file_observations[event_id] for event_id in file_event_order
                ),
                terminal=previous_total or pre_cumulative_usage,
            )
            streams_by_session.setdefault(session_id, []).append(stream)

    # Count repeated candidate events independently from stream selection. A
    # repeated event can prove a full prefix, but one shared snapshot alone is
    # not evidence that the remaining branches are additive.
    candidate_event_ids: set[str] = set()
    for streams in streams_by_session.values():
        for stream in streams:
            for observation in stream.observations:
                if observation.event_id in candidate_event_ids:
                    stats.duplicate_events_removed += 1
                else:
                    candidate_event_ids.add(observation.event_id)

    selected_streams: list[_CodexFileStream] = []
    for streams in streams_by_session.values():
        selected = max(streams, key=lambda stream: stream.score)
        for stream in streams:
            if stream is selected:
                continue
            if _codex_streams_are_prefix_compatible(stream, selected):
                continue
            stats.malformed_usage_events += 1
            message = (
                "Multiple files share a Codex session ID without a provable "
                "complete-prefix relationship; only the stream with the "
                "largest decoded subtotal was retained."
            )
            if message not in stats.limitations:
                stats.limitations.append(message)
        selected_streams.append(selected)

    # Explicit response IDs should also be unique across selected sessions.
    # Retain one conservative observation on a collision and mark conflicting
    # metadata/deltas partial rather than summing both.
    observations: dict[str, UsageObservation] = {}
    for stream in selected_streams:
        for observation in stream.observations:
            previous = observations.get(observation.event_id)
            if previous is None:
                observations[observation.event_id] = observation
                continue
            if previous != observation:
                stats.malformed_usage_events += 1
                if observation.tokens.total_tokens < previous.tokens.total_tokens:
                    observations[observation.event_id] = observation
    ordered = tuple(
        sorted(
            observations.values(),
            key=lambda item: (item.observed_at, item.session_id, item.event_id),
        )
    )
    stats.observation_start = None
    stats.observation_end = None
    for observation in ordered:
        stats.note_observation(observation)
    return UsageDataset(ordered, (stats.coverage(len(ordered)),))


def _claude_tokens(raw: Mapping[str, Any]) -> TokenUsage | None:
    fields = {
        "input": _present_counter(raw, "input_tokens", "inputTokens"),
        "output": _present_counter(raw, "output_tokens", "outputTokens"),
        "read": _present_counter(
            raw,
            "cache_read_input_tokens",
            "cacheReadInputTokens",
            "cached_input_tokens",
            "cachedInputTokens",
        ),
        "write": _present_counter(
            raw,
            "cache_creation_input_tokens",
            "cacheCreationInputTokens",
            "cache_write_input_tokens",
            "cacheWriteInputTokens",
        ),
    }
    cache_creation = _mapping(
        _first(raw, "cache_creation", "cacheCreation")
    ) or {}
    five_present, five = _present_counter(
        cache_creation,
        "ephemeral_5m_input_tokens",
        "ephemeral5mInputTokens",
    )
    hour_present, hour = _present_counter(
        cache_creation,
        "ephemeral_1h_input_tokens",
        "ephemeral1hInputTokens",
    )
    if not any(present for present, _ in fields.values()) and not (
        five_present or hour_present
    ):
        return None
    values = [value for _, value in fields.values()] + [five, hour]
    if any(value is None for value in values):
        return None
    input_tokens = fields["input"][1] or 0
    output_tokens = fields["output"][1] or 0
    cached = fields["read"][1] or 0
    cache_write = fields["write"][1] or 0
    five = five or 0
    hour = hour or 0
    if five + hour > cache_write:
        # Some versions omit the aggregate but retain the detail.
        if fields["write"][0]:
            return None
        cache_write = five + hour
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        cache_write_tokens=cache_write,
        cache_write_5m_tokens=five,
        cache_write_1h_tokens=hour,
    )


def scan_claude_usage(
    projects_root: Path | str,
    *,
    scanned_at: datetime | None = None,
) -> UsageDataset:
    """Scan per-response usage metadata in Claude Code project transcripts."""

    root = Path(projects_root).expanduser()
    if root.is_file() or root.name == ".credentials.json":
        root = root.parent / "projects"
    elif root.name == ".claude":
        root = root / "projects"
    now = scanned_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stats = _ScanStats(
        provider="claude",
        source_label="LOCAL CLAUDE HISTORY",
        location_hint=_path_hint(root),
        scanned_at=now,
        limitations=[
            "Totals cover retained Claude Code transcripts on this profile/device.",
            "History retained across sign-ins cannot be separated by account or billing mode.",
        ],
    )
    files = _safe_jsonl_files((root,), stats)
    stats.files_discovered = len(files)
    observations: dict[str, UsageObservation] = {}

    for path in files:
        if stats.reading_stopped:
            break
        fallback_session = path.stem
        for record in _records(path, stats):
            message = _mapping(record.get("message"))
            if record.get("type") != "assistant":
                continue
            if message is None:
                stats.malformed_usage_events += 1
                continue
            if "usage" not in message:
                stats.usage_events_seen += 1
                stats.malformed_usage_events += 1
                continue
            usage_raw = _mapping(message.get("usage"))
            if usage_raw is None:
                stats.usage_events_seen += 1
                stats.malformed_usage_events += 1
                continue
            stats.usage_events_seen += 1
            tokens = _claude_tokens(usage_raw)
            moment = _timestamp(
                _first(record, "timestamp", "created_at", "createdAt")
                or _first(message, "timestamp", "created_at", "createdAt")
            )
            if tokens is None or moment is None:
                stats.malformed_usage_events += 1
                continue
            session_value = _first(record, "sessionId", "session_id", "session")
            normalized_session = _identifier(session_value, max_length=200)
            session_id = normalized_session or fallback_session
            if session_value is not None and normalized_session is None:
                stats.malformed_usage_events += 1
            model = _model_name(_first(message, "model", "model_name", "modelName"))
            raw_message_id = _first(message, "id", "message_id", "messageId")
            raw_outer_id = _first(record, "uuid", "id")
            raw_request_id = _first(record, "requestId", "request_id")
            message_id = _identifier(raw_message_id)
            outer_id = _identifier(raw_outer_id)
            request_id = _identifier(raw_request_id)
            if (raw_message_id is not None and message_id is None) or (
                raw_message_id is None
                and raw_outer_id is not None
                and outer_id is None
            ) or (
                raw_message_id is None
                and raw_outer_id is None
                and raw_request_id is not None
                and request_id is None
            ):
                stats.malformed_usage_events += 1
            identity = (
                ("message", message_id)
                if message_id is not None
                else ("record", outer_id)
                if outer_id is not None
                else ("request", request_id)
                if request_id is not None
                else (
                    "fallback",
                    session_id,
                    moment.isoformat(),
                    model,
                    tokens.input_tokens,
                    tokens.cached_input_tokens,
                    tokens.cache_write_tokens,
                    tokens.output_tokens,
                )
            )
            # Anthropic message/record IDs survive transcript branches, so the
            # stable event key intentionally does not include the container
            # session ID.
            event_id = _stable_id("claude", *identity)
            observation = UsageObservation(
                provider="claude",
                model=model,
                observed_at=moment,
                tokens=tokens,
                session_id=session_id,
                event_id=event_id,
            )
            previous = observations.get(event_id)
            if previous is not None:
                stats.duplicate_events_removed += 1
                # Streaming/final transcript variants can repeat one message
                # ID. Keep the most complete counter set, never sum both.
                previous_identity = _token_identity(previous.tokens)
                current_identity = _token_identity(tokens)
                comparable = all(
                    left <= right
                    for left, right in zip(
                        previous_identity,
                        current_identity,
                        strict=True,
                    )
                ) or all(
                    left >= right
                    for left, right in zip(
                        previous_identity,
                        current_identity,
                        strict=True,
                    )
                )
                if previous.model != observation.model or not comparable:
                    stats.malformed_usage_events += 1
                previous_score = (
                    previous.tokens.total_tokens,
                    previous.tokens.cache_write_5m_tokens
                    + previous.tokens.cache_write_1h_tokens,
                    previous.observed_at,
                )
                current_score = (
                    tokens.total_tokens,
                    tokens.cache_write_5m_tokens + tokens.cache_write_1h_tokens,
                    moment,
                )
                if current_score > previous_score:
                    observations[event_id] = observation
                continue
            observations[event_id] = observation

    ordered = tuple(
        sorted(
            observations.values(),
            key=lambda item: (item.observed_at, item.session_id, item.event_id),
        )
    )
    # Determine coverage from the final deduplicated records so replacements do
    # not leave a stale start/end timestamp.
    for observation in ordered:
        stats.note_observation(observation)
    return UsageDataset(ordered, (stats.coverage(len(ordered)),))


# Concise aliases for callers that already know the source is local.
scan_codex_history = scan_codex_usage
scan_claude_history = scan_claude_usage


__all__ = [
    "scan_claude_history",
    "scan_claude_usage",
    "scan_codex_history",
    "scan_codex_usage",
]
