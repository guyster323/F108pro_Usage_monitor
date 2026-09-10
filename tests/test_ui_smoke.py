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
