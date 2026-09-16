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


def test_upload_worker_builds_initial_preview_when_flash_write_is_deferred() -> None:
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import Worker

    class Runtime:
        def __init__(self) -> None:
            self.state = SimpleNamespace(
                last_frames=[],
                last_polled=["fresh-snapshot"],
            )

        def tick(self, *, force: bool) -> str:
            assert not force
            return "deferred: cooldown 120s"

        def build_frames(self, snapshots):
            assert snapshots == ["fresh-snapshot"]
            return ["preview-frame"]

    app = QApplication.instance() or QApplication([])
    runtime = Runtime()
    messages: list[str] = []
    worker = Worker(runtime, force=False)
    worker.done.connect(messages.append)

    worker.run()

    assert runtime.state.last_frames == ["preview-frame"]
    assert messages == ["deferred: cooldown 120s"]
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


def test_main_window_fits_design_size_and_keeps_account_list() -> None:
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow, cycle_seconds
    from quotadeck.config import AccountConfig, AppConfig
    from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config = AppConfig(
        accounts=[
            AccountConfig("codex", "demo", "TEAM", True),
            AccountConfig("cursor", "demo2", "CUR", True),
            AccountConfig("claude", "demo3", "CLD", True),
        ]
    )
    window.reload_accounts()
    window.resize(920, 620)
    window.show()
    app.processEvents()
    assert window.sizeHint().width() <= 920
    assert window.sizeHint().height() <= 620
    assert window.list.minimumHeight() >= 140
    assert window.list.height() >= 140
    assert window.preview.width() >= LCD_WIDTH
    assert window.preview.height() >= LCD_HEIGHT
    assert window.preview.width() <= 720 or window.height() > 620
    assert cycle_seconds(3, 5) == 15
    assert window.hold_cycle.text()
    assert "15" in window.hold_cycle.text()
    assert window.hold_hint.toolTip()
    assert "15" in window.hold_hint.toolTip()
    for button in (
        window.accounts_hint_btn,
        window.hold_hint,
        window.flash_hint,
        window.keyboard_hint,
        window.lock_hint,
        window.order_hint,
    ):
        assert button.accessibleName()
        assert button.accessibleDescription()
        assert button.statusTip()
        assert button.toolTip()
        assert button.whatsThis()
        assert button.focusPolicy().name == "StrongFocus"
    window.close()
    assert app is not None


def test_smart_and_fixed_labels_use_icons_and_hover_copy() -> None:
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.core.models import DisplayMode

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config.ui_language = "en"
    window.apply_language()
    smart = window.mode.findData(DisplayMode.SMART.value)
    fixed = window.mode.findData(DisplayMode.FIXED.value)
    assert "Smart" in window.mode.itemText(smart)
    assert "Fixed" in window.mode.itemText(fixed)
    assert not window.mode.itemIcon(smart).isNull()
    assert not window.mode.itemIcon(fixed).isNull()
    window.mode.setCurrentIndex(smart)
    assert "urgent" in window.mode.toolTip().lower() or "ratio" in window.mode.toolTip().lower()
    window.mode.setCurrentIndex(fixed)
    assert "list order" in window.mode.toolTip().lower()
    window.close()
    assert app is not None


def test_lock_hover_distinguishes_offline_stale_and_tls_usage_error() -> None:
    from datetime import datetime, timezone

    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.core.models import UsageSnapshot

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config.ui_language = "en"
    window.apply_language()
    fetched = datetime.now(timezone.utc)

    window.update_lock_status(
        [
            UsageSnapshot(
                "cursor",
                "user-1",
                "USER",
                "pro",
                [],
                "offline",
                fetched,
                error="Cursor is not signed in",
            )
        ]
    )
    offline = window.lock_hint.toolTip().lower()
    assert "not signed in" in offline
    assert "usage fetch failed" not in offline

    window.update_lock_status(
        [
            UsageSnapshot(
                "cursor",
                "user-1",
                "USER",
                "pro",
                [],
                "stale",
                fetched,
                error="401 unauthorized",
            )
        ]
    )
    stale = window.lock_hint.toolTip().lower()
    assert "expired" in stale
    assert "usage fetch failed" not in stale

    window.update_lock_status(
        [
            UsageSnapshot(
                "cursor",
                "user-1",
                "USER",
                "pro",
                [],
                "error",
                fetched,
                error="TLS verification failed usage-summary GetCurrentPeriodUsage",
            )
        ]
    )
    usage = window.lock_hint.toolTip().lower()
    assert "usage fetch failed" in usage
    assert "signed in" in usage
    assert "not signed in" not in usage
    window.close()
    assert app is not None


def test_cursor_row_uses_compact_source_menu_with_tooltips() -> None:
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import AccountRow
    from quotadeck.config import AccountConfig

    app = QApplication.instance() or QApplication([])
    row = AccountRow(AccountConfig("cursor", "work", "WORK"))
    row.apply_language("en", source="")
    assert not row.actions_btn.isHidden()
    assert row.actions_btn.maximumWidth() <= 88
    assert row.actions_btn.focusPolicy().name == "StrongFocus"
    assert row.actions_btn.accessibleName()
    assert row.actions_btn.toolTip()
    labels = [action.text() for action in row.actions_menu.actions()]
    assert any("CSV" in text for text in labels)
    assert any("Enterprise API" in text or "Admin" in text for text in labels)
    assert not hasattr(row, "connect_btn")
    row.apply_language("en", source="csv")
    assert row.actions_btn.text() == "CSV"
    assert "CSV" in row.actions_btn.toolTip()
    labels = [action.text() for action in row.actions_menu.actions()]
    assert "Disconnect" in labels
    assert any("Admin" in text or "Enterprise" in text for text in labels)
    row.apply_language("ko", source="admin_api")
    assert row.actions_btn.text() == "API"
    assert row.actions_btn.toolTip()
    assert app is not None


def test_hold_cycle_counts_visible_cumulative_accounts_only() -> None:
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.config import AccountConfig, AppConfig
    from quotadeck.core.models import MetricMode
    from quotadeck.usage.display import CumulativeSnapshot
    from quotadeck.usage.models import UsagePeriod

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config = AppConfig(
        accounts=[AccountConfig("cursor", "work", "CUR", True)],
        metric_mode=MetricMode.CUMULATIVE,
        scene_hold_seconds=5,
    )
    window.reload_accounts()
    index = window.metric_combo.findData("cumulative")
    window.metric_combo.setCurrentIndex(index if index >= 0 else 1)
    period = str(window.period.currentData() or window.config.cumulative_period.value)
    window.runtime.state.last_cumulative[("cursor:work", period)] = (
        CumulativeSnapshot.unsupported(
            provider="cursor",
            account_id="work",
            display_name="CUR",
            period=UsagePeriod(period) if period in {"daily", "monthly"} else UsagePeriod.MONTHLY,
        )
    )
    window.update_hold_cycle()
    assert window._lcd_enabled_count() == 0
    assert "0" in window.hold_cycle.text()
    window.close()
    assert app is not None


def test_admin_connect_queues_one_cache_preview_without_second_live_fetch(
    monkeypatch,
) -> None:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    import quotadeck.app.main_window as main_window
    from quotadeck.config import AccountConfig, AppConfig, CursorUsageBinding
    from quotadeck.core.models import MetricMode

    app = QApplication.instance() or QApplication([])
    window = main_window.MainWindow(live=False)
    window.config = AppConfig(
        accounts=[AccountConfig("cursor", "work", "CUR", True)],
        metric_mode=MetricMode.CUMULATIVE,
    )
    window.reload_accounts()
    monkeypatch.setattr(main_window, "save_config", lambda _config: None)
    started: list[str] = []
    monkeypatch.setattr(
        window,
        "_start_thread",
        lambda worker, kind: started.append(kind),
    )
    admin_calls = {"n": 0}

    def boom_collect(*_args, **_kwargs):
        admin_calls["n"] += 1
        raise AssertionError("queued preview must reuse the Admin cache")

    monkeypatch.setattr(
        "quotadeck.usage.cursor_admin.collect_cursor_admin",
        boom_collect,
    )

    force_flags: list[bool] = []
    original_refresh = window._refresh_cursor_runtime

    def spy_refresh(*, force_admin: bool = False) -> None:
        force_flags.append(force_admin)
        original_refresh(force_admin=force_admin)

    window._refresh_cursor_runtime = spy_refresh  # type: ignore[method-assign]

    placeholder = QThread(window)
    window.admin_worker = placeholder
    window._active_threads.add(placeholder)
    window.set_busy(True)
    window._live = True

    window._on_cursor_admin_validated("work", "dev@company.com")

    binding = window.config.cursor_binding("work")
    assert binding is not None
    assert isinstance(binding, CursorUsageBinding)
    assert binding.source == "admin_api"
    assert binding.admin_email == "dev@company.com"
    assert window._display_refresh_pending is True
    assert started == []
    assert force_flags == [False]
    assert admin_calls["n"] == 0

    window._finish_thread(placeholder, "cursor-admin-connect")
    assert window._busy is False
    assert window.admin_worker is None
    from PySide6.QtCore import QElapsedTimer

    waited = QElapsedTimer()
    waited.start()
    while started == [] and waited.elapsed() < 500:
        app.processEvents()
    assert started == ["display-refresh"]
    assert window._display_refresh_pending is False
    assert admin_calls["n"] == 0
    assert force_flags == [False]

    recorded: list[bool] = []
    window._refresh_cursor_runtime = (  # type: ignore[method-assign]
        lambda *, force_admin=False: recorded.append(force_admin)
    )
    cursor_row = next(row for row in window.rows if row.account.provider == "cursor")
    cursor_row.apply_language("en", source="admin_api")
    cursor_row.update_admin.emit()
    assert recorded == [False]

    window.set_busy(False)
    window._live = False
    window.close()
    assert app is not None


def test_cursor_csv_guide_cancel_does_not_open_picker_or_mutate(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QFileDialog

    from quotadeck.app.main_window import MainWindow
    from quotadeck.config import AccountConfig, AppConfig

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config = AppConfig(
        accounts=[AccountConfig("cursor", "work", "WORK", True)]
    )
    window.reload_accounts()
    before = window.collect_config()
    opened: list[str] = []
    monkeypatch.setattr(window, "_confirm_cursor_csv_guide", lambda: False)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: opened.append("picker") or ("", ""),
    )
    window._import_cursor_csv("work")
    assert opened == []
    after = window.collect_config()
    assert [item.account_id for item in after.accounts] == [
        item.account_id for item in before.accounts
    ]
    assert after.cursor_bindings == before.cursor_bindings
    window.close()
    assert app is not None


def test_cursor_csv_guide_continue_reaches_picker_and_cancel_stays_clean(
    monkeypatch,
) -> None:
    from PySide6.QtWidgets import QApplication, QFileDialog

    from quotadeck.app.main_window import MainWindow
    from quotadeck.config import AccountConfig, AppConfig

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config = AppConfig(
        accounts=[AccountConfig("cursor", "work", "WORK", True)]
    )
    window.reload_accounts()
    before = window.collect_config()
    opened: list[str] = []
    monkeypatch.setattr(window, "_confirm_cursor_csv_guide", lambda: True)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: opened.append("picker") or ("", ""),
    )
    window._import_cursor_csv("work")
    assert opened == ["picker"]
    after = window.collect_config()
    assert after.cursor_bindings == before.cursor_bindings
    window.close()
    assert app is not None


def test_cursor_admin_guide_cancel_does_not_prompt_or_mutate(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QInputDialog

    from quotadeck.app.main_window import MainWindow
    from quotadeck.config import AccountConfig, AppConfig

    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    window.config = AppConfig(
        accounts=[AccountConfig("cursor", "work", "WORK", True)]
    )
    window.reload_accounts()
    before = window.collect_config()
    prompted: list[str] = []
    monkeypatch.setattr(window, "_confirm_cursor_admin_guide", lambda: False)
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *args, **kwargs: prompted.append("key") or ("secret", True),
    )
    window._connect_cursor_admin("work")
    assert prompted == []
    after = window.collect_config()
    assert after.cursor_bindings == before.cursor_bindings
    window.close()
    assert app is not None


def test_cursor_guide_dialogs_expose_official_links_and_accessible_buttons(
    monkeypatch,
) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    import quotadeck.app.main_window as main_window

    app = QApplication.instance() or QApplication([])
    opened: list[str] = []
    monkeypatch.setattr(
        main_window,
        "open_official_url",
        lambda url: opened.append(url) or True,
    )

    clicks = {"n": 0}

    class _ScriptedBox(QMessageBox):
        def exec(self) -> int:  # noqa: A003
            clicks["n"] += 1
            if clicks["n"] == 1:
                link = next(
                    button
                    for button in self.buttons()
                    if "Analytics" in button.text() or "대시보드" in button.text()
                )
                assert link.accessibleName()
                self._clicked = link
            else:
                cancel = next(
                    button
                    for button in self.buttons()
                    if button.text() in {"Cancel", "취소"}
                )
                assert cancel.accessibleName()
                self._clicked = cancel
            return 0

        def clickedButton(self):  # noqa: N802
            return self._clicked

    monkeypatch.setattr(main_window, "QMessageBox", _ScriptedBox)
    assert main_window.show_cursor_guide_dialog(
        None,
        "en",
        title_key="cursor_csv_guide_title",
        body_key="cursor_csv_guide_body",
        links=(("cursor_csv_guide_open", main_window.CURSOR_ANALYTICS_URL),),
        continue_key="cursor_csv_guide_continue",
        cancel_key="cursor_csv_guide_cancel",
    ) is False
    assert opened == [main_window.CURSOR_ANALYTICS_URL]
    assert "cursor.com/dashboard/analytics" in opened[0]

    clicks["n"] = 0
    opened.clear()

    class _AdminBox(_ScriptedBox):
        def exec(self) -> int:  # noqa: A003
            clicks["n"] += 1
            labels = [button.text() for button in self.buttons()]
            assert any("API docs" in text or "API 문서" in text for text in labels)
            assert any("Admin API" in text for text in labels)
            if clicks["n"] == 1:
                link = next(button for button in self.buttons() if "Admin API" in button.text())
                assert link.accessibleName()
                self._clicked = link
            elif clicks["n"] == 2:
                link = next(
                    button
                    for button in self.buttons()
                    if "API docs" in button.text() or button.text() == "API 문서 열기"
                )
                self._clicked = link
            else:
                self._clicked = next(
                    button for button in self.buttons() if button.text() in {"Cancel", "취소"}
                )
            return 0

    monkeypatch.setattr(main_window, "QMessageBox", _AdminBox)
    assert main_window.show_cursor_guide_dialog(
        None,
        "en",
        title_key="cursor_admin_guide_title",
        body_key="cursor_admin_guide_body",
        links=(
            ("cursor_admin_guide_open_api", main_window.CURSOR_API_DOCS_URL),
            ("cursor_admin_guide_open_admin", main_window.CURSOR_ADMIN_API_DOCS_URL),
        ),
        continue_key="cursor_admin_guide_continue",
        cancel_key="cursor_admin_guide_cancel",
    ) is False
    assert main_window.CURSOR_ADMIN_API_DOCS_URL in opened
    assert main_window.CURSOR_API_DOCS_URL in opened
    assert app is not None
