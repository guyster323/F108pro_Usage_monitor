from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


def test_main_window_lists_accounts() -> None:
    from PySide6.QtWidgets import QApplication

    from quotadeck.app.main_window import MainWindow
    from quotadeck.config import AccountConfig, AppConfig
    app = QApplication.instance() or QApplication([])
    window = MainWindow(live=False)
    assert window.hold.value() == 5
    assert window.min_up.value() == 30
    assert window.lbl_upload.text() == "Flash 주기"
    assert window.poll.display.text() == "60"
    assert window.hold.display.text() == "5"
    assert window.min_up.display.text() == "30"
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
    window.close()
    del window
    assert app is not None


def test_timing_fields_accept_direct_input(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QLineEdit

    import quotadeck.app.main_window as main_window_module

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main_window_module, "save_config", lambda config: None)
    monkeypatch.setattr(main_window_module, "set_launch_at_startup", lambda enabled: None)
    window = main_window_module.MainWindow(live=False)

    assert isinstance(window.poll.display, QLineEdit)
    window.poll.display.setText("900")
    window.hold.display.setText("7")
    window.min_up.display.setText("45")
    assert window.apply()

    collected = window.collect_config()
    assert collected.poll_seconds == 900
    assert collected.scene_hold_seconds == 7
    assert collected.min_upload_minutes == 45
    window.close()
    assert app is not None


def test_timing_fields_show_popup_for_out_of_range(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication

    import quotadeck.app.main_window as main_window_module

    app = QApplication.instance() or QApplication([])
    messages: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: messages.append(message),
    )
    window = main_window_module.MainWindow(live=False)

    for control, value, bounds in (
        (window.poll, "14", "15"),
        (window.hold, "21", "20"),
        (window.min_up, "0", "1"),
    ):
        control.display.setText(value)
        assert not window.apply()
        assert messages and bounds in messages[-1]
        control.display.setText(str(control.value()))

    window.close()
    assert app is not None
