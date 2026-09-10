from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from quotadeck.config import AppConfig
from quotadeck.core.models import AccountRef, Severity, UsageSnapshot, UsageWindow
from quotadeck.core.scheduler import QuotaDeckRuntime, render_hash
from quotadeck.core.statefile import PersistedState, load_state, save_state


def _snapshot(remaining_first: float, remaining_second: float) -> UsageSnapshot:
    now = datetime.now(timezone.utc)
    return UsageSnapshot(
        provider="cursor",
        account_id="demo",
        display_name="DEMO",
        plan="ultra",
        windows=[
            UsageWindow(id="auto", label="AUTO", used_percent=100 - remaining_first, remaining_percent=remaining_first),
            UsageWindow(id="other", label="OTHER", used_percent=100 - remaining_second, remaining_percent=remaining_second),
        ],
        status="ok",
        fetched_at=now,
    )


def test_render_hash_tracks_non_minimum_displayed_window() -> None:
    """AUTO 80→60 with OTHER fixed at 20 changes pixels, so it must change the hash."""
    sevs = {"cursor:demo": Severity.BUSY}
    a = render_hash([_snapshot(80, 20)], sevs, theme="crew", mode="smart")
    b = render_hash([_snapshot(60, 20)], sevs, theme="crew", mode="smart")
    assert a != b


def test_render_hash_still_quantizes_small_changes() -> None:
    sevs = {"cursor:demo": Severity.BUSY}
    a = render_hash([_snapshot(82, 20)], sevs, theme="crew", mode="smart")
    b = render_hash([_snapshot(81, 20)], sevs, theme="crew", mode="smart")
    assert a == b


def test_update_config_preserves_budget(tmp_path: Path) -> None:
    runtime = QuotaDeckRuntime(AppConfig(), mock=True, state_path=tmp_path / "state.json")
    runtime.budget.record()
    runtime.state.last_hash = "abcd"
    assert runtime.budget.uploads_today == 1
    new_config = AppConfig(min_upload_minutes=30, daily_flash_limit=50)
    runtime.update_config(new_config)
    assert runtime.budget.uploads_today == 1
    assert runtime.budget.last_upload is not None
    assert runtime.state.last_hash == "abcd"
    assert runtime.budget.min_interval == timedelta(minutes=30)
    assert runtime.budget.daily_limit == 50


def test_max_age_does_not_bypass_min_interval(tmp_path: Path) -> None:
    config = AppConfig(min_upload_minutes=120, max_age_minutes=60)
    runtime = QuotaDeckRuntime(config, mock=True, state_path=tmp_path / "state.json")
    runtime.budget.last_upload = datetime.now(timezone.utc) - timedelta(minutes=61)
    runtime.budget.day = datetime.now(timezone.utc).date()
    runtime.budget.uploads_today = 1
    assert runtime.budget.stale()  # 61min > max_age 60min
    result = runtime.maybe_upload([_snapshot(80, 20)])
    assert not result.uploaded
    assert result.code == "cooldown"
    assert result.next_allowed is not None


def test_force_upload_respects_daily_limit(tmp_path: Path) -> None:
    config = AppConfig(daily_flash_limit=1)
    runtime = QuotaDeckRuntime(config, mock=True, state_path=tmp_path / "state.json")
    runtime.budget.record()
    result = runtime.maybe_upload([_snapshot(80, 20)], force=True)
    assert not result.uploaded
    assert result.code == "daily_limit"


def test_empty_snapshot_list_never_uploads(tmp_path: Path) -> None:
    runtime = QuotaDeckRuntime(AppConfig(), mock=True, state_path=tmp_path / "state.json")
    result = runtime.maybe_upload([], force=True)
    assert not result.uploaded
    assert result.code == "no_accounts"


class _FakeProvider:
    def __init__(self, provider_id: str, *, fail: bool = False) -> None:
        self.id = provider_id
        self.fail = fail
        self.account = AccountRef(
            provider=provider_id,
            account_id="acct",
            display_name=provider_id.upper(),
            source_path="",
        )

    def discover(self) -> list[AccountRef]:
        return [self.account]

    def fetch(self, account: AccountRef) -> UsageSnapshot:
        if self.fail:
            raise RuntimeError("boom")
        return UsageSnapshot(
            provider=self.id,
            account_id="acct",
            display_name=self.id.upper(),
            plan=None,
            windows=[UsageWindow(id="w", label="5H", used_percent=10, remaining_percent=90)],
            status="ok",
            fetched_at=datetime.now(timezone.utc),
        )


def test_poll_isolates_account_failure(tmp_path: Path) -> None:
    runtime = QuotaDeckRuntime(AppConfig(), mock=True, state_path=tmp_path / "state.json")
    bad = _FakeProvider("bad", fail=True)
    good = _FakeProvider("good")
    runtime.providers = {"bad": bad, "good": good}
    snapshots = runtime.poll()
    assert len(snapshots) == 2
    by_key = {snap.key: snap for snap in snapshots}
    assert by_key["good:acct"].status == "ok"
    assert by_key["bad:acct"].status == "error"
    assert runtime.state.severity["bad:acct"] == Severity.ERROR
    assert "bad:acct" in runtime.state.last_errors
    # last good snapshot table only holds successful fetches
    assert "good:acct" in runtime.state.previous
    assert "bad:acct" not in runtime.state.previous


def test_budget_state_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # mock upload writes preview.gif into cwd
    state_path = tmp_path / "state.json"
    first = QuotaDeckRuntime(AppConfig(), mock=True, state_path=state_path)
    result = first.maybe_upload([_snapshot(80, 20)], force=True)
    assert result.uploaded
    second = QuotaDeckRuntime(AppConfig(), mock=True, state_path=state_path)
    assert second.budget.uploads_today == 1
    assert second.budget.last_upload is not None
    assert second.state.last_hash == first.state.last_hash
    # the very next automatic tick must not rewrite an unchanged screen
    again = second.maybe_upload([_snapshot(80, 20)])
    assert again.code == "unchanged"


def test_statefile_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    now = datetime.now(timezone.utc)
    save_state(PersistedState(last_upload=now, uploads_today=3, day=now.date(), last_hash="ff"), path)
    loaded = load_state(path)
    assert loaded.uploads_today == 3
    assert loaded.day == now.date()
    assert loaded.last_hash == "ff"
    assert loaded.last_upload is not None and abs((loaded.last_upload - now).total_seconds()) < 1


def test_statefile_corrupt_returns_fresh(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    loaded = load_state(path)
    assert loaded.uploads_today == 0
    assert loaded.last_upload is None
