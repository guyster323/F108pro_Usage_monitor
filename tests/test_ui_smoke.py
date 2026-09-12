from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


def test_main_window_lists_accounts() -> None:
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.config import AccountConfig, AppConfig
    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    assert window.hold.value() == 5
    window.config = AppConfig(
        accounts=[
            AccountConfig("codex", "demo", "TEAM", True, source_kind="cli", source_label="Codex CLI"),
            AccountConfig("cursor", "demo2", "CUR", True, source_kind="app", source_label="Cursor App"),
        ]
    )
    window.reload_accounts()
    assert window.list.count() == 2
    window.hold.setValue(7)
    collected = window.collect_config()
    assert [item.alias for item in collected.accounts] == ["TEAM", "CUR"]
    assert collected.accounts[0].source_kind == "cli"
    assert collected.accounts[1].source_kind == "app"
    assert collected.scene_hold_seconds == 7
    window.set_busy(True)
    assert not window.upload_btn.isEnabled()
    window.set_busy(False)
    assert window.upload_btn.isEnabled()
    window.config.ui_language = "en"
    window.apply_language()
    assert window.upload_btn.text() == "Upload to keyboard"

    # Result callbacks must not reopen the action buttons until Qt confirms
    # that the underlying QThread has actually finished.
    placeholder = QThread(window)
    window._active_threads.add(placeholder)
    window.set_busy(True)
    window._on_preview([], [])
    assert window._busy is True
    window._finish_thread(placeholder, "preview")
    assert window._busy is False

    close_event = QCloseEvent()
    window.closeEvent(close_event)
    assert close_event.isAccepted()
    window.close()
    del window
    assert app is not None


def test_stepper_clamps_its_initial_value() -> None:
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.stepper import Stepper

    app = QApplication.instance() or QApplication([])
    assert Stepper(minimum=1, maximum=120, value=0).value() == 1
    assert Stepper(minimum=1, maximum=120, value=999).value() == 120
    assert app is not None


def test_account_row_clears_previous_value_style_and_metadata() -> None:
    from datetime import datetime, timezone

    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import AccountRow
    from quotadeck.config import AccountConfig
    from quotadeck.core.models import UsageSnapshot, UsageWindow

    app = QApplication.instance() or QApplication([])
    row = AccountRow(
        AccountConfig("codex", "one", "ONE", source_label="Codex CLI")
    )
    row.set_usage(
        UsageSnapshot(
            "codex",
            "one",
            "ONE",
            "plus",
            [UsageWindow("session", "5H", 99, 1)],
            "ok",
            datetime.now(timezone.utc),
        )
    )
    assert row.remaining.styleSheet()
    assert "5H" in row.meta.text()

    row.set_usage(None)
    assert row.remaining.text() == "--%"
    assert row.remaining.styleSheet() == ""
    assert row.meta.text() == "CODEX CLI"
    assert app is not None


def test_tray_menu_is_retained_and_quit_defers_for_tracked_worker() -> None:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.app.tray import attach_tray

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    tray = attach_tray(window)
    window.tray = tray
    window._live = True
    try:
        assert tray.contextMenu() is window.tray_menu
        assert window.tray_menu.parent() is window
        assert window.tray_logs in window.tray_menu.actions()

        window.set_busy(True)
        assert not window.tray_quit.isEnabled()
        window.set_busy(False)
        assert window.tray_quit.isEnabled()

        window.hide()
        window.tray_show.trigger()
        assert window.isVisible()

        placeholder = QThread(window)
        window._active_threads.add(placeholder)
        called: list[bool] = []
        original_quit = window._quit_now
        window._quit_now = lambda: called.append(True)  # type: ignore[method-assign]
        window.close_app()
        assert called == []
        assert window._quit_requested is True
        window._active_threads.clear()
        window._quit_now = original_quit  # type: ignore[method-assign]
    finally:
        tray.hide()
        window._live = False
        window._quit_requested = False
        window.close()
        app.processEvents()


def test_refresh_schedule_is_single_shot_non_reentrant_and_stops_for_quit() -> None:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow

    class FakeTimer:
        def __init__(self) -> None:
            self.starts: list[int] = []
            self.stops = 0

        def start(self, delay: int) -> None:
            self.starts.append(delay)

        def stop(self) -> None:
            self.stops += 1

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    timer = FakeTimer()
    window._live = True
    window._refresh_timer = timer  # type: ignore[assignment]
    window.config.poll_seconds = 17

    window._schedule_refresh()
    assert timer.starts == [17_000]

    # A fired timer never starts a second worker while another operation owns
    # the runtime; it simply arms the next single-shot interval.
    placeholder = QThread(window)
    window._active_threads.add(placeholder)
    window.set_busy(True)
    window._automatic_refresh()
    assert timer.starts == [17_000, 17_000]
    assert window.worker is None
    window._active_threads.clear()
    window.set_busy(False)

    window._quit_now = lambda: None  # type: ignore[method-assign]
    window.close_app()
    assert timer.stops == 1
    starts_at_quit = list(timer.starts)
    window._schedule_refresh(0)
    assert timer.starts == starts_at_quit
    window._live = False
    window._quit_requested = False
    window.close()
    assert app is not None


def test_automatic_refresh_uses_non_forced_worker_when_keyboard_is_ready(
    monkeypatch,
) -> None:
    from PySide6.QtWidgets import QApplication

    import quotadeck.app.main_window as main_window

    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(live=False)
    monkeypatch.setattr(main_window, "enumerate_interfaces", lambda: [object()])
    monkeypatch.setattr(main_window, "wired_mode_ok", lambda _items: True)
    monkeypatch.setattr(main_window, "aula_software_running", lambda: [])
    started: list[tuple[object, str]] = []
    monkeypatch.setattr(
        window,
        "_start_thread",
        lambda worker, kind: started.append((worker, kind)),
    )

    window._automatic_refresh()

    assert len(started) == 1
    assert started[0][1] == "automatic-upload"
    assert isinstance(started[0][0], main_window.Worker)
    assert started[0][0].force is False
    window.set_busy(False)
    window.close()
    assert app is not None


def test_apply_reuses_identical_runtime(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication

    import quotadeck.app.main_window as main_window

    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(live=False)
    original = window.runtime
    monkeypatch.setattr(main_window, "save_config", lambda _config: None)
    monkeypatch.setattr(main_window, "set_launch_at_startup", lambda _enabled: None)
    monkeypatch.setattr(
        main_window,
        "QuotaDeckRuntime",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("identical settings recreated the runtime")
        ),
    )

    assert window.apply()
    assert window.runtime is original
    window.close()
    assert app is not None


def test_event_loop_startup_does_not_shadow_paint_device_metric(
    monkeypatch, tmp_path
) -> None:
    """Qt calls QMainWindow.metric() during show/paint.

    A QComboBox stored as ``self.metric`` shadows that virtual and the event
    loop reports TypeError: QComboBox object is not callable.
    """

    import sys
    from datetime import timedelta

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QPaintDevice
    from PySide6.QtWidgets import QApplication, QComboBox

    import quotadeck.app.main_window as main_window
    from quotadeck.config import AccountConfig, AppConfig, save_config
    from quotadeck.core.flashbudget import FlashBudget
    from quotadeck.core.scheduler import SchedulerState

    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(main_window, "enumerate_interfaces", lambda: [])
    monkeypatch.setattr(main_window, "wired_mode_ok", lambda _items: False)
    monkeypatch.setattr(main_window, "aula_software_running", lambda: [])
    monkeypatch.setattr(main_window, "discover_accounts", lambda: [])
    monkeypatch.setattr(main_window, "set_launch_at_startup", lambda _enabled: None)

    class FakeRuntime:
        def __init__(self, config: AppConfig) -> None:
            self.config = config
            self.state = SchedulerState()
            self.budget = FlashBudget(
                min_interval=timedelta(minutes=config.min_upload_minutes),
                max_age=timedelta(minutes=config.max_age_minutes),
                daily_limit=config.daily_flash_limit,
            )
            self.mock = True

        def poll(self) -> list:
            return []

        def build_frames(self, snapshots) -> list:
            return []

        def tick(self, *, force: bool = False) -> str:
            return "skipped: mock"

    monkeypatch.setattr(main_window, "QuotaDeckRuntime", FakeRuntime)
    save_config(
        AppConfig(
            accounts=[
                AccountConfig(
                    "codex",
                    "demo",
                    "TEAM",
                    True,
                    source_kind="cli",
                    source_label="Codex CLI",
                )
            ],
            launch_at_startup=False,
        )
    )

    caught: list[BaseException] = []

    def sys_hook(exc_type, exc, _tb) -> None:
        caught.append(exc)

    def unraisable_hook(args) -> None:
        if args.exc_value is not None:
            caught.append(args.exc_value)

    monkeypatch.setattr(sys, "excepthook", sys_hook)
    monkeypatch.setattr(sys, "unraisablehook", unraisable_hook)

    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(live=False)
    try:
        assert isinstance(window.metric_combo, QComboBox)
        assert callable(window.metric)
        assert not isinstance(window.metric, QComboBox)

        window.show()
        window.set_busy(True)
        window.status.setText(window._t("status_reading"))
        loop = QEventLoop(window)
        QTimer.singleShot(150, loop.quit)
        loop.exec()

        pdm_width = window.metric(QPaintDevice.PaintDeviceMetric.PdmWidth)
        assert isinstance(pdm_width, int)
        assert pdm_width > 0
        assert window.logicalDpiX() > 0
        assert window.widthMM() >= 0
        assert window.isVisible()
        assert window.windowTitle()
        collected = window.collect_config()
        assert collected.metric_mode.value in {"quota", "cumulative"}
        assert [item.alias for item in collected.accounts] == ["TEAM"]
        assert not any(
            isinstance(exc, TypeError) and "QComboBox" in str(exc) for exc in caught
        )
        assert caught == []
    finally:
        window.set_busy(False)
        window.close()
        app.processEvents()
        assert app is not None
