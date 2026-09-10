from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from quotadeck.config import AccountConfig, AppConfig  # noqa: E402
from quotadeck.core.scheduler import UploadResult  # noqa: E402


class FakeRuntime:
    def __init__(self, config: AppConfig, *, poll_delay: float = 0.0) -> None:
        self.config = config
        self.poll_delay = poll_delay
        self.polls = 0
        self.upload_forces: list[bool] = []
        self.config_updates: list[AppConfig] = []

    def poll(self):
        if self.poll_delay:
            time.sleep(self.poll_delay)
        self.polls += 1
        return []

    def build_frames(self, snapshots):
        return []

    def maybe_upload(self, snapshots, *, force: bool = False) -> UploadResult:
        self.upload_forces.append(force)
        return UploadResult(uploaded=False, code="unchanged")

    def update_config(self, config: AppConfig) -> None:
        self.config_updates.append(config)
        self.config = config


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait_until(app, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _make_controller(runtime):
    from quotadeck.app.refresh_controller import RefreshController

    return RefreshController(runtime)


def test_manual_refresh_polls_without_upload() -> None:
    app = _app()
    runtime = FakeRuntime(AppConfig())
    controller = _make_controller(runtime)
    polled = []
    controller.polled.connect(lambda snaps, frames: polled.append(snaps))
    controller.request_refresh()
    assert _wait_until(app, lambda: polled and not controller.is_busy())
    assert runtime.polls == 1
    assert runtime.upload_forces == []
    controller.shutdown()


def test_upload_request_forces() -> None:
    app = _app()
    runtime = FakeRuntime(AppConfig())
    controller = _make_controller(runtime)
    results = []
    controller.upload_done.connect(lambda result, manual: results.append((result.code, manual)))
    controller.request_upload()
    assert _wait_until(app, lambda: results and not controller.is_busy())
    assert runtime.upload_forces == [True]
    assert results == [("unchanged", True)]
    controller.shutdown()


def test_start_schedules_periodic_auto_poll() -> None:
    app = _app()
    config = AppConfig(
        accounts=[AccountConfig(provider="codex", account_id="a", alias="A")],
        poll_seconds=15,  # minimum allowed; timer interval only needs to be armed
    )
    runtime = FakeRuntime(config)
    controller = _make_controller(runtime)
    controller.start()
    assert _wait_until(app, lambda: runtime.polls >= 1 and not controller.is_busy())
    # saved account selection exists, so the startup tick evaluates upload policy
    assert runtime.upload_forces == [False]
    assert controller._timer.isActive()
    assert controller.next_poll is not None
    controller.shutdown()
    assert not controller._timer.isActive()


def test_first_run_does_not_auto_upload() -> None:
    app = _app()
    runtime = FakeRuntime(AppConfig())  # no saved accounts: first run
    controller = _make_controller(runtime)
    controller.start()
    assert _wait_until(app, lambda: runtime.polls >= 1 and not controller.is_busy())
    assert runtime.upload_forces == []
    controller.shutdown()


def test_requests_coalesce_while_busy() -> None:
    app = _app()
    runtime = FakeRuntime(AppConfig(), poll_delay=0.3)
    controller = _make_controller(runtime)
    controller.request_refresh()
    assert _wait_until(app, controller.is_busy, timeout=2.0)
    for _ in range(4):
        controller.request_refresh()
    assert _wait_until(app, lambda: runtime.polls >= 2 and not controller.is_busy())
    app.processEvents()
    assert runtime.polls == 2  # one running + at most one queued
    controller.shutdown()


def test_config_update_deferred_until_job_finishes() -> None:
    app = _app()
    runtime = FakeRuntime(AppConfig(), poll_delay=0.3)
    controller = _make_controller(runtime)
    controller.request_refresh()
    assert _wait_until(app, controller.is_busy, timeout=2.0)
    new_config = AppConfig(poll_seconds=120)
    controller.update_config(new_config)
    assert runtime.config_updates == []  # not applied while the worker runs
    assert _wait_until(app, lambda: not controller.is_busy())
    assert runtime.config_updates == [new_config]
    controller.shutdown()
