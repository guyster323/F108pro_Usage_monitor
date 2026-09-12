from __future__ import annotations

import json
import os
import sys
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quotadeck.usage.grok import (
    GROK_COST_TICKS_PER_USD,
    MAX_GROK_USAGE_INTEGER,
    GrokCapability,
    GrokOtelApiEvent,
    GrokUsageFormatError,
    _minimal_child_environment,
    _run_bounded_command,
    build_grok_otel_dataset,
    discover_grok_session_ids,
    grok_external_otel_environment_hint,
    grok_session_to_records,
    grok_usage_cache_path,
    parse_grok_usage_payload,
    reported_cost_ticks,
    scan_grok_session_usage,
)
from quotadeck.usage.models import UsageDataset, UsageSourceKind


def _summary(
    *,
    input_tokens: int = 120,
    output_tokens: int = 30,
    cached: int = 20,
    created: int = 10,
    reasoning: int = 5,
    cost_ticks: int | None = 25_000_000_000,
    primary_model_id: str | None = "grok-4.6",
    cost_is_partial: bool = False,
    usage_is_incomplete: bool = False,
) -> dict[str, object]:
    value: dict[str, object] = {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": input_tokens + output_tokens,
        "cachedReadTokens": cached,
        "cacheCreationTokens": created,
        "reasoningTokens": reasoning,
        "modelCalls": 2,
        "turnCount": 1,
    }
    if primary_model_id is not None:
        value["primaryModelId"] = primary_model_id
    if cost_ticks is not None:
        value["costUsdTicks"] = cost_ticks
    if cost_is_partial:
        value["costIsPartial"] = True
    if usage_is_incomplete:
        value["usageIsIncomplete"] = True
    return value


def _payload(session_id: str = "session-1") -> dict[str, object]:
    session = _summary()
    session["modelUsage"] = {"grok-4.6": _summary()}
    turn = _summary()
    turn.update(
        {
            "turnNumber": 1,
            "endedAt": "2026-09-11T12:34:50Z",
            "modelUsage": {"grok-4.6": _summary()},
        }
    )
    return {
        "sessionId": session_id,
        "updatedAt": "2026-09-11T12:34:56Z",
        "session": session,
        "turns": [turn],
    }


def test_official_usage_payload_normalizes_inclusive_input_and_cost() -> None:
    parsed = parse_grok_usage_payload(_payload())

    assert parsed.tokens.input_tokens == 90
    assert parsed.tokens.cached_input_tokens == 20
    assert parsed.tokens.cache_write_tokens == 10
    assert parsed.tokens.output_tokens == 30
    assert parsed.tokens.total_tokens == 150
    assert parsed.tokens.reasoning_output_tokens == 5
    assert parsed.cost_usd == Decimal("2.5")
    assert parsed.cost_usd_ticks == 25_000_000_000
    assert parsed.summary.primary_model_id == "grok-4.6"
    assert parsed.turns[0].ended_at == datetime(
        2026, 9, 11, 12, 34, 50, tzinfo=timezone.utc
    )
    assert not hasattr(parsed.turns[0], "prompt_id")
    assert parsed.per_model_complete
    assert parsed.model_totals[0].model == "grok-4.6"


def test_command_updated_at_is_metadata_not_a_usage_observation() -> None:
    parsed = parse_grok_usage_payload(_payload())

    assert parsed.updated_at == datetime(2026, 9, 11, 12, 34, 56, tzinfo=timezone.utc)
    assert not hasattr(parsed.turns[0], "observed_at")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("totalTokens", 999),
        ("reasoningTokens", 31),
        ("cachedReadTokens", 121),
    ],
)
def test_usage_payload_rejects_inconsistent_token_shapes(
    field: str, value: int
) -> None:
    payload = _payload()
    payload["session"][field] = value  # type: ignore[index]

    with pytest.raises(GrokUsageFormatError):
        parse_grok_usage_payload(payload)


def test_unofficial_cache_aliases_are_ignored() -> None:
    payload = _payload()
    payload["session"]["cacheReadTokens"] = 19  # type: ignore[index]
    payload["session"]["cachedInputTokens"] = 18  # type: ignore[index]
    payload["session"]["cacheWriteTokens"] = 9  # type: ignore[index]
    payload["turns"][0]["promptId"] = "not-an-official-field"  # type: ignore[index]

    parsed = parse_grok_usage_payload(payload)

    assert parsed.tokens.cached_input_tokens == 20
    assert parsed.tokens.cache_write_tokens == 10
    assert not hasattr(parsed.turns[0], "prompt_id")


def test_usage_payload_rejects_integer_above_bounded_counter_range() -> None:
    payload = _payload()
    payload["session"]["inputTokens"] = MAX_GROK_USAGE_INTEGER + 1  # type: ignore[index]

    with pytest.raises(GrokUsageFormatError, match="bounded"):
        parse_grok_usage_payload(payload)


def test_malformed_model_map_does_not_discard_exact_session_total() -> None:
    payload = _payload()
    payload["session"]["modelUsage"] = {"grok-4.6": {"inputTokens": "bad"}}  # type: ignore[index]

    parsed = parse_grok_usage_payload(payload)

    assert parsed.tokens.total_tokens == 150
    assert parsed.model_totals == ()
    assert not parsed.per_model_complete


def test_model_map_must_reconcile_with_authoritative_session_total() -> None:
    payload = _payload()
    payload["session"]["modelUsage"] = {  # type: ignore[index]
        "grok-4.6": _summary(
            input_tokens=1,
            output_tokens=1,
            cached=0,
            created=0,
            reasoning=0,
        )
    }

    parsed = parse_grok_usage_payload(payload)

    assert parsed.tokens.total_tokens == 150
    assert parsed.model_totals == ()
    assert not parsed.per_model_complete


def test_turn_rows_must_reconcile_with_authoritative_session_total() -> None:
    payload = _payload()
    payload["turns"] = []

    parsed = parse_grok_usage_payload(payload)

    assert parsed.tokens.total_tokens == 150
    assert parsed.turns == ()
    assert not parsed.turns_complete


def test_malformed_turn_is_partial_not_an_invented_zero() -> None:
    payload = _payload()
    payload["turns"].append({"turnNumber": 2, "inputTokens": "bad"})  # type: ignore[union-attr]

    parsed = parse_grok_usage_payload(payload)

    assert len(parsed.turns) == 1
    assert not parsed.turns_complete
    assert parsed.tokens.total_tokens == 150


def test_session_discovery_reads_names_not_conversation_content(tmp_path: Path) -> None:
    for session_id in ("b-session", "a-session"):
        summary = tmp_path / "sessions" / "cwd" / session_id / "summary.json"
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text("not even parsed", encoding="utf-8")

    assert discover_grok_session_ids(tmp_path) == ("a-session", "b-session")


def test_session_discovery_rejects_option_like_ids_without_running_them(
    tmp_path: Path,
) -> None:
    summary = tmp_path / "sessions" / "cwd" / "--help" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"{}", stderr=b"")

    result = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        cache_dir=tmp_path / "qd-cache",
    )

    assert calls == []
    assert [issue.code for issue in result.issues] == ["invalid_session_id"]
    assert result.dataset.coverages[0].is_partial


def test_session_discovery_budget_marks_inventory_partial(tmp_path: Path) -> None:
    for name in ("one", "two"):
        path = tmp_path / "sessions" / name / "nested" / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")

    result = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        max_discovery_entries=1,
        cache_dir=tmp_path / "qd-cache",
    )

    assert result.sessions == ()
    assert result.dataset.coverages[0].scan_truncated
    assert result.dataset.coverages[0].is_partial


def test_scanner_uses_official_command_without_shell_and_never_daily_dates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    summary = tmp_path / "sessions" / "cwd" / "session-1" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text("{}", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setenv("QUOTADECK_GROK_SECRET", "do-not-forward")

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(_payload()), stderr="")

    result = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        now=datetime(2026, 9, 11, tzinfo=timezone.utc),
        cache_dir=tmp_path / "qd-cache",
    )

    assert calls[0][0] == ["grok-test", "usage", "session-1"]
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["stderr"] is subprocess.DEVNULL
    assert calls[0][1]["env"]["GROK_HOME"] == str(tmp_path)
    assert "QUOTADECK_GROK_SECRET" not in calls[0][1]["env"]
    assert result.has_exact_session_totals
    assert not result.can_aggregate_account
    assert result.sessions[0].tokens.total_tokens == 150
    assert result.dataset.observations == ()
    coverage = result.dataset.coverages[0]
    assert coverage.usage_events_seen == 1
    assert coverage.observations_emitted == 0
    assert coverage.observation_start is None
    assert coverage.observation_end is None
    assert result.session_records[0].timestamp is None
    assert result.session_records[0].session == "session-1"
    assert result.session_records[0].reported_cost_usd == Decimal("2.5")
    assert result.web_unsupported.source == "grok_web"


def test_scanner_never_retains_command_error_text(tmp_path: Path) -> None:
    summary = tmp_path / "sessions" / "cwd" / "session-1" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text("{}", encoding="utf-8")
    secret = "xai-do-not-retain"

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=secret)

    result = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        cache_dir=tmp_path / "qd-cache",
    )

    assert result.sessions == ()
    assert result.issues[0].code == "command_failed"
    assert secret not in repr(result)


def test_scanner_classifies_json_integer_digit_limit_as_invalid_output(
    tmp_path: Path,
) -> None:
    summary = tmp_path / "sessions" / "cwd" / "session-1" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text("{}", encoding="utf-8")
    huge_integer_json = (
        '{"sessionId":"session-1","updatedAt":"2026-09-11T12:34:56Z",'
        '"session":{"inputTokens":' + ("9" * 5000) + "}}"
    )

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=huge_integer_json, stderr="")

    result = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        cache_dir=tmp_path / "qd-cache",
    )

    assert result.sessions == ()
    assert [issue.code for issue in result.issues] == ["invalid_output"]


def test_scanner_does_not_call_runner_when_executable_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    summary = tmp_path / "sessions" / "cwd" / "session-1" / "summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("quotadeck.usage.grok.find_executable", lambda _name: None)

    result = scan_grok_session_usage(
        tmp_path, cache_dir=tmp_path / "qd-cache"
    )

    assert [issue.code for issue in result.issues] == ["command_not_found"]
    assert result.dataset.coverages[0].read_errors == 1


def test_production_command_reader_stops_at_byte_cap(tmp_path: Path) -> None:
    result = _run_bounded_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 4096); sys.stdout.flush()",
        ],
        timeout_seconds=3.0,
        max_output_bytes=128,
        env=_minimal_child_environment(tmp_path),
    )

    assert result.issue_code == "output_too_large"
    assert len(result.stdout) == 0


def test_support_contract_does_not_claim_account_or_daily_local_ledger(
    tmp_path: Path,
) -> None:
    result = scan_grok_session_usage(
        tmp_path, executable="unused", cache_dir=tmp_path / "qd-cache"
    )

    assert result.support.session_totals is GrokCapability.EXACT
    assert result.support.account_total is GrokCapability.UNAVAILABLE
    assert result.support.daily is GrokCapability.CONDITIONAL
    assert result.support.one_year is GrokCapability.CONDITIONAL


def test_otel_v1_adapter_produces_timestamped_per_model_usage() -> None:
    at = datetime(2026, 9, 11, 8, 30, tzinfo=timezone.utc)
    event = GrokOtelApiEvent(
        observed_at=at,
        user_id="user-1",
        session_id="session-1",
        event_sequence=7,
        model="grok-4.6",
        inclusive_input_tokens=100,
        cached_read_tokens=40,
        output_tokens=20,
        reasoning_tokens=5,
    )

    dataset = build_grok_otel_dataset([event], expected_user_id="user-1")

    assert len(dataset.observations) == 1
    observation = dataset.observations[0]
    assert observation.observed_at == at
    assert observation.model == "grok-4.6"
    assert observation.tokens.input_tokens == 60
    assert observation.tokens.cached_input_tokens == 40
    assert observation.tokens.total_tokens == 120
    assert observation.source_kind is UsageSourceKind.EXTERNAL_OTEL


def test_otel_adapter_requires_one_explicit_identity() -> None:
    event = GrokOtelApiEvent(
        observed_at=datetime.now(timezone.utc),
        user_id="user-2",
        session_id="session-1",
        event_sequence=1,
        model="grok-4.6",
        inclusive_input_tokens=1,
        output_tokens=1,
    )

    with pytest.raises(ValueError, match="user_id"):
        build_grok_otel_dataset([event], expected_user_id="user-1")


def test_otel_adapter_deduplicates_exact_sequence_and_rejects_conflicts() -> None:
    at = datetime.now(timezone.utc)
    event = GrokOtelApiEvent(at, "u", "s", 1, "grok-4.6", 10, 2)
    dataset = build_grok_otel_dataset([event, event], expected_user_id="u")
    assert len(dataset.observations) == 1
    assert dataset.coverages[0].duplicate_events_removed == 1

    conflicting = GrokOtelApiEvent(at, "u", "s", 1, "grok-4.6", 11, 2)
    with pytest.raises(ValueError, match="conflicting"):
        build_grok_otel_dataset([event, conflicting], expected_user_id="u")


def test_otel_event_ids_do_not_collide_across_sessions_when_datasets_merge() -> None:
    at = datetime.now(timezone.utc)
    first = GrokOtelApiEvent(at, "u", "session-a", 1, "grok-4.6", 10, 2)
    second = GrokOtelApiEvent(at, "u", "session-b", 1, "grok-4.6", 11, 3)

    first_dataset = build_grok_otel_dataset([first], expected_user_id="u")
    second_dataset = build_grok_otel_dataset([second], expected_user_id="u")
    merged = UsageDataset.merge(first_dataset, second_dataset)

    assert len(merged.observations) == 2
    assert len({item.event_id for item in merged.observations}) == 2


def test_external_otel_hint_reads_only_opt_in_and_exporter_names() -> None:
    env = {
        "GROK_EXTERNAL_OTEL": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=super-secret",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example",
    }

    hint = grok_external_otel_environment_hint(env)

    assert hint.metrics_requested
    assert "super-secret" not in repr(hint)
    assert "collector.example" not in repr(hint)


def test_cost_tick_constant_matches_official_scale() -> None:
    assert Decimal(GROK_COST_TICKS_PER_USD) == Decimal("1e10")
    assert reported_cost_ticks(25_000_000_000) == 25_000_000_000
    assert Decimal(25_000_000_000) / Decimal(GROK_COST_TICKS_PER_USD) == Decimal("2.5")


def test_zero_and_partial_cost_ticks_are_unknown() -> None:
    assert reported_cost_ticks(0) is None
    assert reported_cost_ticks(-1) is None

    zero = _payload()
    zero["session"]["costUsdTicks"] = 0  # type: ignore[index]
    assert parse_grok_usage_payload(zero).cost_usd is None

    partial = _payload()
    zero_session = _summary(cost_is_partial=True)
    partial["session"] = zero_session
    parsed = parse_grok_usage_payload(partial)
    assert parsed.summary.cost_is_partial
    assert parsed.cost_usd is None
    assert parsed.cost_usd_ticks is None

    incomplete = _payload()
    incomplete["session"] = _summary(usage_is_incomplete=True)
    parsed_incomplete = parse_grok_usage_payload(incomplete)
    assert parsed_incomplete.summary.usage_is_incomplete
    assert parsed_incomplete.cost_usd is None


def test_headless_json_is_rejected_by_grok_usage_parser() -> None:
    headless = {
        "usage": {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 16,
            "cache_read_tokens": 1,
            "cache_creation_tokens": 1,
        },
        "total_cost_usd": 0.01,
        "total_cost_usd_ticks": 1_000_000_000,
    }
    with pytest.raises(GrokUsageFormatError):
        parse_grok_usage_payload(headless)


def _seed_session(root: Path, session_id: str = "session-1") -> Path:
    directory = root / "sessions" / "cwd" / session_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text("{}", encoding="utf-8")
    usage = directory / "usage.json"
    usage.write_text("{}", encoding="utf-8")
    return directory


def test_unchanged_usage_json_is_served_from_disk_cache(tmp_path: Path) -> None:
    _seed_session(tmp_path)
    cache_dir = tmp_path / "qd-cache"
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(_payload()), stderr=""
        )

    first = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        cache_dir=cache_dir,
    )
    second = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        cache_dir=cache_dir,
    )

    assert len(calls) == 1
    assert first.cache_hits == 0
    assert second.cache_hits == 1
    assert second.sessions[0].tokens.total_tokens == 150
    cache_file = grok_usage_cache_path(cache_dir, tmp_path, "session-1")
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    dumped = json.dumps(cached).casefold()
    assert "prompt" not in dumped
    assert "api_key" not in dumped
    assert "authorization" not in dumped


def test_changed_usage_json_misses_cache_and_corrupt_cache_falls_back(
    tmp_path: Path,
) -> None:
    directory = _seed_session(tmp_path)
    usage = directory / "usage.json"
    cache_dir = tmp_path / "qd-cache"
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(_payload()), stderr=""
        )

    scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        cache_dir=cache_dir,
    )
    os.utime(usage, ns=(usage.stat().st_mtime_ns + 2_000_000_000,) * 2)
    changed = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        cache_dir=cache_dir,
    )
    assert changed.cache_hits == 0
    assert len(calls) == 2

    cache_file = grok_usage_cache_path(cache_dir, tmp_path, "session-1")
    cache_file.write_text("{not-json", encoding="utf-8")
    recovered = scan_grok_session_usage(
        tmp_path,
        executable="grok-test",
        runner=runner,
        cache_dir=cache_dir,
    )
    assert recovered.cache_hits == 0
    assert len(calls) == 3
    assert recovered.sessions[0].cost_usd == Decimal("2.5")


def test_turn_ended_at_is_the_only_usage_timestamp() -> None:
    parsed = parse_grok_usage_payload(_payload())
    records = grok_session_to_records(parsed, include_turns=True)
    session_record, turn_record = records
    assert session_record.timestamp is None
    assert turn_record.timestamp == datetime(
        2026, 9, 11, 12, 34, 50, tzinfo=timezone.utc
    )
    assert turn_record.timestamp != parsed.updated_at
    assert turn_record.turn == 1
