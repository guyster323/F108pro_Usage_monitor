from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import quotadeck.usage.local as usage_local
from quotadeck.usage.local import scan_claude_usage, scan_codex_usage


def _write_jsonl(path: Path, rows: list[object], *, broken_tail: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n"
    if broken_tail:
        text += "{broken-json\n"
    path.write_text(text, encoding="utf-8")


def _codex_total(
    timestamp: str,
    *,
    raw_input: int,
    cached: int,
    output: int,
    reasoning: int = 0,
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": raw_input,
                    "cached_input_tokens": cached,
                    "output_tokens": output,
                    "reasoning_output_tokens": reasoning,
                    "total_tokens": raw_input + output,
                }
            },
        },
    }


def test_codex_scans_live_and_archived_without_double_counting(tmp_path: Path) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": "session-1"}},
        {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
        _codex_total(
            "2026-09-10T12:00:00Z", raw_input=100, cached=40, output=20, reasoning=5
        ),
        _codex_total(
            "2026-09-11T12:00:00Z", raw_input=160, cached=50, output=30, reasoning=6
        ),
    ]
    _write_jsonl(tmp_path / "sessions" / "2026" / "rollout.jsonl", rows)
    _write_jsonl(tmp_path / "archived_sessions" / "rollout.jsonl", rows)

    dataset = scan_codex_usage(
        tmp_path, scanned_at=datetime(2026, 9, 11, tzinfo=timezone.utc)
    )

    assert len(dataset.observations) == 2
    assert dataset.observations[0].tokens.input_tokens == 60
    assert dataset.observations[0].tokens.cached_input_tokens == 40
    assert dataset.observations[0].tokens.output_tokens == 20
    assert dataset.observations[1].tokens.total_tokens == 70
    assert {item.model for item in dataset.observations} == {"gpt-5.6-sol"}
    coverage = dataset.coverages[0]
    assert coverage.files_discovered == 2
    assert coverage.files_read == 2
    assert coverage.duplicate_events_removed == 2
    assert not coverage.is_partial


def test_codex_fallback_identity_does_not_merge_distinct_sessions(
    tmp_path: Path,
) -> None:
    timestamp = "2026-09-11T12:00:00Z"
    for session_id, filename in (("session-a", "a.jsonl"), ("session-b", "b.jsonl")):
        rows = [
            {"type": "session_meta", "payload": {"id": session_id}},
            _codex_total(timestamp, raw_input=100, cached=40, output=20),
        ]
        _write_jsonl(tmp_path / "sessions" / filename, rows)

    dataset = scan_codex_usage(tmp_path)
    assert len(dataset.observations) == 2
    assert {item.session_id for item in dataset.observations} == {
        "session-a",
        "session-b",
    }


def test_codex_reconciles_truncated_and_complete_session_copies(
    tmp_path: Path,
) -> None:
    first = _codex_total(
        "2026-09-10T12:00:00Z", raw_input=100, cached=0, output=0
    )
    final = _codex_total(
        "2026-09-11T12:00:00Z", raw_input=200, cached=0, output=0
    )
    session = {"type": "session_meta", "payload": {"id": "shared-session"}}
    _write_jsonl(tmp_path / "sessions" / "s.jsonl", [session, final])
    _write_jsonl(
        tmp_path / "archived_sessions" / "s.jsonl",
        [session, first, final],
    )

    dataset = scan_codex_usage(tmp_path)
    assert [item.tokens.total_tokens for item in dataset.observations] == [100, 100]
    coverage = dataset.coverages[0]
    assert coverage.duplicate_events_removed == 1
    assert coverage.malformed_usage_events == 1
    assert coverage.is_partial


def test_codex_disjoint_files_with_one_session_id_are_partial(
    tmp_path: Path,
) -> None:
    session = {"type": "session_meta", "payload": {"id": "rotated-session"}}
    _write_jsonl(
        tmp_path / "sessions" / "a.jsonl",
        [
            session,
            _codex_total(
                "2026-09-10T12:00:00Z", raw_input=100, cached=0, output=0
            ),
        ],
    )
    _write_jsonl(
        tmp_path / "sessions" / "b.jsonl",
        [
            session,
            _codex_total(
                "2026-09-11T12:00:00Z", raw_input=200, cached=0, output=0
            ),
        ],
    )

    dataset = scan_codex_usage(tmp_path)
    assert len(dataset.observations) == 1
    assert dataset.observations[0].tokens.total_tokens == 200
    coverage = dataset.coverages[0]
    assert coverage.malformed_usage_events == 1
    assert coverage.is_partial
    assert any("complete-prefix" in item for item in coverage.limitations)


def test_codex_does_not_union_divergent_streams_with_one_shared_snapshot(
    tmp_path: Path,
) -> None:
    session = {"type": "session_meta", "payload": {"id": "forked-session"}}
    shared = _codex_total(
        "2026-09-11T10:00:00Z", raw_input=100, cached=0, output=0
    )
    _write_jsonl(
        tmp_path / "sessions" / "a.jsonl",
        [
            session,
            shared,
            _codex_total(
                "2026-09-11T10:01:00Z", raw_input=200, cached=0, output=0
            ),
        ],
    )
    _write_jsonl(
        tmp_path / "archived_sessions" / "b.jsonl",
        [
            session,
            shared,
            _codex_total(
                "2026-09-11T10:02:00Z", raw_input=300, cached=0, output=0
            ),
        ],
    )

    dataset = scan_codex_usage(tmp_path)

    assert [item.tokens.total_tokens for item in dataset.observations] == [100, 200]
    assert sum(item.tokens.total_tokens for item in dataset.observations) == 300
    coverage = dataset.coverages[0]
    assert coverage.duplicate_events_removed == 1
    assert coverage.malformed_usage_events == 1
    assert coverage.is_partial
    assert any("complete-prefix" in item for item in coverage.limitations)


def test_codex_prefers_decoded_subtotal_when_one_copy_resets(
    tmp_path: Path,
) -> None:
    session = {"type": "session_meta", "payload": {"id": "reset-session"}}
    _write_jsonl(
        tmp_path / "sessions" / "reset.jsonl",
        [
            session,
            _codex_total(
                "2026-09-11T10:00:00Z", raw_input=100, cached=0, output=0
            ),
            _codex_total(
                "2026-09-11T10:01:00Z", raw_input=50, cached=0, output=0
            ),
        ],
    )
    _write_jsonl(
        tmp_path / "archived_sessions" / "alternate.jsonl",
        [
            session,
            _codex_total(
                "2026-09-11T10:02:00Z", raw_input=120, cached=0, output=0
            ),
        ],
    )

    dataset = scan_codex_usage(tmp_path)

    assert [item.tokens.total_tokens for item in dataset.observations] == [100, 50]
    assert sum(item.tokens.total_tokens for item in dataset.observations) == 150
    coverage = dataset.coverages[0]
    assert coverage.counter_resets_seen == 1
    assert coverage.malformed_usage_events == 1
    assert coverage.is_partial
    assert any("complete-prefix" in item for item in coverage.limitations)


def test_codex_preserves_aggregate_delta_when_cache_categories_move(
    tmp_path: Path,
) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": "session-2"}},
        {"type": "turn_context", "payload": {"model": "gpt-5.6-terra"}},
        _codex_total(
            "2026-09-11T10:00:00Z", raw_input=160, cached=50, output=30
        ),
        _codex_total(
            "2026-09-11T10:01:00Z", raw_input=170, cached=20, output=35
        ),
    ]
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", rows)

    dataset = scan_codex_usage(tmp_path)
    delta = dataset.observations[1].tokens
    assert delta.input_tokens == 0
    assert delta.cached_input_tokens == 0
    assert delta.unclassified_input_tokens == 10
    assert delta.output_tokens == 5
    assert delta.total_tokens == 15


def test_codex_does_not_add_last_only_inside_cumulative_stream(
    tmp_path: Path,
) -> None:
    rows = [
        _codex_total(
            "2026-09-11T10:00:00Z", raw_input=100, cached=0, output=0
        ),
        {
            "timestamp": "2026-09-11T10:01:00Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 50,
                        "cached_input_tokens": 0,
                        "output_tokens": 0,
                    }
                },
            },
        },
        _codex_total(
            "2026-09-11T10:02:00Z", raw_input=200, cached=0, output=0
        ),
    ]
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", rows)

    dataset = scan_codex_usage(tmp_path)
    assert [item.tokens.total_tokens for item in dataset.observations] == [100, 100]
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial
    assert dataset.report(
        today=datetime(2026, 9, 11, tzinfo=timezone.utc).date(),
        timezone_name="UTC",
    ).tokens.total_tokens == 200


def test_codex_reconciles_last_only_records_before_first_cumulative(
    tmp_path: Path,
) -> None:
    def last_only(timestamp: str, amount: int) -> dict[str, object]:
        return {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": amount,
                        "output_tokens": 0,
                    }
                },
            },
        }

    rows = [
        last_only("2026-09-11T10:00:00Z", 40),
        last_only("2026-09-11T10:01:00Z", 10),
        _codex_total(
            "2026-09-11T10:02:00Z", raw_input=100, cached=0, output=0
        ),
    ]
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", rows)

    dataset = scan_codex_usage(tmp_path)
    assert [item.tokens.total_tokens for item in dataset.observations] == [40, 10, 50]
    assert sum(item.tokens.total_tokens for item in dataset.observations) == 100


def test_codex_counter_reset_starts_a_new_increment(tmp_path: Path) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": "session-3"}},
        _codex_total("2026-09-11T10:00:00Z", raw_input=100, cached=0, output=20),
        _codex_total("2026-09-11T10:01:00Z", raw_input=10, cached=0, output=2),
    ]
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", rows)

    dataset = scan_codex_usage(tmp_path)
    assert [item.tokens.total_tokens for item in dataset.observations] == [120, 12]
    assert dataset.coverages[0].counter_resets_seen == 1
    assert not dataset.coverages[0].is_partial


def test_codex_current_token_usage_records_and_cache_writes(tmp_path: Path) -> None:
    first = {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "cache_write_input_tokens": 10,
        "output_tokens": 30,
        "reasoning_output_tokens": 5,
        "total_tokens": 130,
    }
    second = {
        "input_tokens": 150,
        "cached_input_tokens": 30,
        "cache_write_input_tokens": 15,
        "output_tokens": 40,
        "reasoning_output_tokens": 7,
        "total_tokens": 190,
    }
    rows = [
        {"type": "session_meta", "payload": {"id": "session-current"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "thread_settings_applied",
                "thread_settings": {"model": "gpt-5.6-sol"},
            },
        },
        {
            "timestamp": "2026-09-11T10:00:00Z",
            "type": "token_usage_record",
            "payload": {
                "session_id": "session-current",
                "response_id": "response-1",
                "usage": first,
                "turn_token_usage": first,
                "thread_token_usage": first,
            },
        },
        # A legacy notification with the same cumulative value may coexist;
        # sharing the cumulative cursor keeps it from being counted twice.
        {
            "timestamp": "2026-09-11T10:00:01Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": first,
                    "last_token_usage": first,
                },
            },
        },
        {
            "timestamp": "2026-09-11T10:01:00Z",
            "type": "token_usage_record",
            "payload": {
                "session_id": "session-current",
                "response_id": "response-2",
                "usage": second,
                "turn_token_usage": second,
                "thread_token_usage": second,
            },
        },
    ]
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", rows)

    dataset = scan_codex_usage(tmp_path)
    assert len(dataset.observations) == 2
    first_tokens, second_tokens = [item.tokens for item in dataset.observations]
    assert first_tokens.input_tokens == 70
    assert first_tokens.cached_input_tokens == 20
    assert first_tokens.cache_write_tokens == 10
    assert first_tokens.output_tokens == 30
    assert first_tokens.total_tokens == 130
    assert second_tokens.input_tokens == 35
    assert second_tokens.cached_input_tokens == 10
    assert second_tokens.cache_write_tokens == 5
    assert second_tokens.output_tokens == 10
    assert second_tokens.total_tokens == 60
    assert {item.model for item in dataset.observations} == {"gpt-5.6-sol"}


def test_codex_invalid_counters_are_partial_not_zero(tmp_path: Path) -> None:
    rows = [
        {"type": "session_meta", "payload": {"id": "bad"}},
        _codex_total("2026-09-11T10:00:00Z", raw_input=-1, cached=0, output=2),
    ]
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", rows)

    dataset = scan_codex_usage(tmp_path)
    assert dataset.observations == ()
    coverage = dataset.coverages[0]
    assert coverage.usage_events_seen == 1
    assert coverage.malformed_usage_events == 1
    assert coverage.is_partial


def test_codex_inconsistent_reported_total_is_partial(tmp_path: Path) -> None:
    row = _codex_total(
        "2026-09-11T10:00:00Z", raw_input=10, cached=0, output=2
    )
    row["payload"]["info"]["total_token_usage"]["total_tokens"] = 999
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", [row])

    dataset = scan_codex_usage(tmp_path)
    assert dataset.observations == ()
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_explicit_null_counters_are_invalid_not_missing(tmp_path: Path) -> None:
    codex = _codex_total(
        "2026-09-11T10:00:00Z", raw_input=10, cached=0, output=2
    )
    codex["payload"]["info"]["total_token_usage"][
        "cached_input_tokens"
    ] = None
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", [codex])

    codex_dataset = scan_codex_usage(tmp_path)
    assert codex_dataset.observations == ()
    assert codex_dataset.coverages[0].malformed_usage_events == 1
    assert codex_dataset.coverages[0].is_partial


def test_pathological_digit_string_counter_is_partial_not_an_exception(
    tmp_path: Path,
) -> None:
    codex = _codex_total(
        "2026-09-11T10:00:00Z", raw_input=10, cached=0, output=2
    )
    codex["payload"]["info"]["total_token_usage"][
        "input_tokens"
    ] = "9" * 5_000
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", [codex])

    dataset = scan_codex_usage(tmp_path)
    assert dataset.observations == ()
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_pathological_numeric_counter_is_partial_not_an_exception(
    tmp_path: Path,
) -> None:
    codex = _codex_total(
        "2026-09-11T10:00:00Z", raw_input=10, cached=0, output=2
    )
    codex["payload"]["info"]["total_token_usage"]["input_tokens"] = 10**400
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", [codex])

    dataset = scan_codex_usage(tmp_path)
    assert dataset.observations == ()
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_float_counter_cannot_bypass_exact_integer_bound(tmp_path: Path) -> None:
    codex = _codex_total(
        "2026-09-11T10:00:00Z", raw_input=10, cached=0, output=2
    )
    codex["payload"]["info"]["total_token_usage"]["input_tokens"] = 1e308
    _write_jsonl(tmp_path / "sessions" / "rollout.jsonl", [codex])

    dataset = scan_codex_usage(tmp_path)
    assert dataset.observations == ()
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def _claude_assistant(
    timestamp: str,
    *,
    message_id: str,
    model: str = "claude-sonnet-4-5-20250929",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: int = 0,
    cache_write: int = 0,
    five: int = 0,
    hour: int = 0,
) -> dict[str, object]:
    return {
        "type": "assistant",
        "sessionId": "claude-session",
        "requestId": f"request-{message_id}",
        "timestamp": timestamp,
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": five,
                    "ephemeral_1h_input_tokens": hour,
                },
            },
        },
    }


def test_claude_reads_per_model_usage_and_deduplicates_message(tmp_path: Path) -> None:
    first = _claude_assistant(
        "2026-09-11T01:00:00Z",
        message_id="msg-1",
        input_tokens=10,
        output_tokens=5,
        cache_read=20,
        cache_write=30,
        five=10,
        hour=20,
    )
    final = _claude_assistant(
        "2026-09-11T01:00:01Z",
        message_id="msg-1",
        input_tokens=10,
        output_tokens=8,
        cache_read=20,
        cache_write=30,
        five=10,
        hour=20,
    )
    second = _claude_assistant(
        "2026-09-11T02:00:00Z",
        message_id="msg-2",
        model="claude-opus-4-1-20250805",
        input_tokens=4,
        output_tokens=6,
    )
    _write_jsonl(tmp_path / "project" / "session.jsonl", [first, final, second])

    dataset = scan_claude_usage(tmp_path)
    assert len(dataset.observations) == 2
    assert dataset.observations[0].tokens.total_tokens == 68
    assert dataset.observations[0].tokens.cache_write_5m_tokens == 10
    assert dataset.observations[0].tokens.cache_write_1h_tokens == 20
    assert dataset.observations[1].model == "claude-opus-4-1-20250805"
    assert dataset.coverages[0].duplicate_events_removed == 1


def test_claude_message_id_deduplicates_across_branch_request_ids(
    tmp_path: Path,
) -> None:
    first = _claude_assistant("2026-09-11T01:00:00Z", message_id="msg-shared")
    copied = _claude_assistant("2026-09-11T01:00:00Z", message_id="msg-shared")
    copied["sessionId"] = "copied-branch"
    copied["requestId"] = "different-request-container"
    _write_jsonl(tmp_path / "project-a" / "session.jsonl", [first])
    _write_jsonl(tmp_path / "project-b" / "session.jsonl", [copied])

    dataset = scan_claude_usage(tmp_path)
    assert len(dataset.observations) == 1
    assert dataset.coverages[0].duplicate_events_removed == 1


def test_claude_conflicting_duplicate_message_is_partial(
    tmp_path: Path,
) -> None:
    first = _claude_assistant(
        "2026-09-11T01:00:00Z",
        message_id="msg-conflict",
        input_tokens=10,
        output_tokens=5,
    )
    conflicting = _claude_assistant(
        "2026-09-11T01:00:01Z",
        message_id="msg-conflict",
        model="claude-opus-4-1-20250805",
        input_tokens=11,
        output_tokens=5,
    )
    _write_jsonl(tmp_path / "project" / "session.jsonl", [first, conflicting])

    dataset = scan_claude_usage(tmp_path)
    assert len(dataset.observations) == 1
    assert dataset.coverages[0].duplicate_events_removed == 1
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_claude_malformed_record_keeps_valid_subtotal_partial(tmp_path: Path) -> None:
    valid = _claude_assistant("2026-09-11T01:00:00Z", message_id="msg-good")
    invalid = _claude_assistant(
        "2026-09-11T02:00:00Z", message_id="msg-bad", input_tokens=-1
    )
    _write_jsonl(
        tmp_path / "project" / "session.jsonl",
        [valid, invalid],
        broken_tail=True,
    )

    dataset = scan_claude_usage(tmp_path)
    assert len(dataset.observations) == 1
    coverage = dataset.coverages[0]
    assert coverage.usage_events_seen == 2
    assert coverage.malformed_usage_events == 2
    assert coverage.is_partial


def test_deeply_nested_json_keeps_valid_subtotal_partial(tmp_path: Path) -> None:
    valid = _claude_assistant("2026-09-11T01:00:00Z", message_id="msg-good")
    path = tmp_path / "project" / "session.jsonl"
    _write_jsonl(path, [valid])
    nested = '{"x":' + "[" * 10_000 + "0" + "]" * 10_000 + "}\n"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(nested)

    dataset = scan_claude_usage(tmp_path)
    assert len(dataset.observations) == 1
    assert dataset.observations[0].tokens.total_tokens == 15
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_extreme_offset_timestamp_is_malformed_not_a_scanner_error(
    tmp_path: Path,
) -> None:
    valid = _claude_assistant("2026-09-11T01:00:00Z", message_id="msg-good")
    invalid = _claude_assistant(
        "0001-01-01T00:00:00+14:00", message_id="msg-bad-time"
    )
    _write_jsonl(tmp_path / "project" / "session.jsonl", [valid, invalid])

    dataset = scan_claude_usage(tmp_path)
    assert len(dataset.observations) == 1
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_claude_explicit_null_counter_is_partial_not_a_zero(tmp_path: Path) -> None:
    invalid = _claude_assistant(
        "2026-09-11T02:00:00Z", message_id="msg-null"
    )
    invalid["message"]["usage"]["input_tokens"] = None
    _write_jsonl(tmp_path / "project" / "session.jsonl", [invalid])

    dataset = scan_claude_usage(tmp_path)
    assert dataset.observations == ()
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_claude_malformed_usage_container_is_partial(tmp_path: Path) -> None:
    invalid = _claude_assistant(
        "2026-09-11T02:00:00Z", message_id="msg-bad-usage"
    )
    invalid["message"]["usage"] = None
    _write_jsonl(tmp_path / "project" / "session.jsonl", [invalid])

    dataset = scan_claude_usage(tmp_path)
    assert dataset.observations == ()
    assert dataset.coverages[0].usage_events_seen == 1
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_claude_missing_usage_metadata_marks_retained_subtotal_partial(
    tmp_path: Path,
) -> None:
    valid = _claude_assistant("2026-09-11T01:00:00Z", message_id="msg-good")
    missing = _claude_assistant("2026-09-11T02:00:00Z", message_id="msg-missing")
    missing["message"].pop("usage")
    _write_jsonl(tmp_path / "project" / "session.jsonl", [valid, missing])

    dataset = scan_claude_usage(tmp_path)
    assert len(dataset.observations) == 1
    assert dataset.coverages[0].usage_events_seen == 2
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_nested_non_string_message_id_cannot_break_identity_hashing(
    tmp_path: Path,
) -> None:
    invalid = _claude_assistant(
        "2026-09-11T02:00:00Z", message_id="placeholder"
    )
    invalid["message"]["id"] = [[[["not-an-id"]]]]
    invalid.pop("requestId")
    _write_jsonl(tmp_path / "project" / "session.jsonl", [invalid])

    dataset = scan_claude_usage(tmp_path)
    assert len(dataset.observations) == 1
    assert dataset.coverages[0].malformed_usage_events == 1
    assert dataset.coverages[0].is_partial


def test_record_budget_marks_valid_subtotal_partial(tmp_path: Path) -> None:
    rows = [
        _claude_assistant("2026-09-11T01:00:00Z", message_id="msg-first"),
        _claude_assistant("2026-09-11T02:00:00Z", message_id="msg-second"),
    ]
    _write_jsonl(tmp_path / "project" / "session.jsonl", rows)
    original = usage_local._MAX_RECORDS_PER_SCAN
    usage_local._MAX_RECORDS_PER_SCAN = 1
    try:
        dataset = scan_claude_usage(tmp_path)
    finally:
        usage_local._MAX_RECORDS_PER_SCAN = original

    assert len(dataset.observations) == 1
    coverage = dataset.coverages[0]
    assert coverage.records_examined == 1
    assert coverage.scan_truncated
    assert coverage.is_partial
    assert any("record-count" in item for item in coverage.limitations)


def test_total_byte_budget_marks_scan_partial(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "project" / "session.jsonl",
        [_claude_assistant("2026-09-11T01:00:00Z", message_id="msg-large")],
    )
    original = usage_local._MAX_TOTAL_JSONL_BYTES
    usage_local._MAX_TOTAL_JSONL_BYTES = 8
    try:
        dataset = scan_claude_usage(tmp_path)
    finally:
        usage_local._MAX_TOTAL_JSONL_BYTES = original

    assert dataset.observations == ()
    coverage = dataset.coverages[0]
    assert coverage.bytes_read == 8
    assert coverage.scan_truncated
    assert coverage.is_partial
    assert any("total-byte" in item for item in coverage.limitations)


def test_file_budget_marks_selected_files_partial(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "project" / "a.jsonl",
        [_claude_assistant("2026-09-11T01:00:00Z", message_id="msg-a")],
    )
    _write_jsonl(
        tmp_path / "project" / "b.jsonl",
        [_claude_assistant("2026-09-11T02:00:00Z", message_id="msg-b")],
    )
    original = usage_local._MAX_FILES_PER_SCAN
    usage_local._MAX_FILES_PER_SCAN = 1
    try:
        dataset = scan_claude_usage(tmp_path)
    finally:
        usage_local._MAX_FILES_PER_SCAN = original

    assert len(dataset.observations) == 1
    coverage = dataset.coverages[0]
    assert coverage.files_discovered == 1
    assert coverage.files_read == 1
    assert coverage.scan_truncated
    assert coverage.is_partial
    assert any("file-count" in item for item in coverage.limitations)


def test_directory_discovery_errors_make_coverage_partial(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    original = usage_local._scandir

    def denied(path: object):
        if Path(path) == root.resolve():
            raise PermissionError("not readable")
        return original(path)

    usage_local._scandir = denied
    try:
        dataset = scan_claude_usage(root)
    finally:
        usage_local._scandir = original

    coverage = dataset.coverages[0]
    assert dataset.observations == ()
    assert coverage.read_errors == 1
    assert coverage.is_partial


def test_scanners_do_not_follow_jsonl_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    _write_jsonl(
        outside,
        [_claude_assistant("2026-09-11T01:00:00Z", message_id="secret")],
    )
    root = tmp_path / "projects"
    root.mkdir()
    link = root / "linked.jsonl"
    try:
        link.symlink_to(outside)
    except OSError:
        return

    dataset = scan_claude_usage(root)
    assert dataset.observations == ()
    assert dataset.coverages[0].files_discovered == 0
    assert dataset.coverages[0].scan_truncated
    assert dataset.coverages[0].is_partial


def test_symlinked_codex_history_root_is_omitted_as_partial(
    tmp_path: Path,
) -> None:
    _write_jsonl(
        tmp_path / "sessions" / "live.jsonl",
        [_codex_total("2026-09-11T01:00:00Z", raw_input=10, cached=0, output=0)],
    )
    external_archive = tmp_path / "elsewhere"
    _write_jsonl(
        external_archive / "old.jsonl",
        [_codex_total("2026-09-10T01:00:00Z", raw_input=100, cached=0, output=0)],
    )
    try:
        (tmp_path / "archived_sessions").symlink_to(
            external_archive,
            target_is_directory=True,
        )
    except OSError:
        return

    dataset = scan_codex_usage(tmp_path)
    assert len(dataset.observations) == 1
    assert dataset.observations[0].tokens.total_tokens == 10
    coverage = dataset.coverages[0]
    assert coverage.scan_truncated
    assert coverage.is_partial
    assert any("symbolic-link history root" in item for item in coverage.limitations)
