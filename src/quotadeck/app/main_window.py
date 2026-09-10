from __future__ import annotations

import sys
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from quotadeck.app.i18n import tr
from quotadeck.app.preview_widget import LcdPreview
from quotadeck.app.refresh_controller import RefreshController
from quotadeck.app.startup import set_launch_at_startup
from quotadeck.app.stepper import Stepper
from quotadeck.app.tray import attach_tray, severity_icon
from quotadeck.config import (
    AccountConfig,
    AppConfig,
    MIN_UPLOAD_MINUTES_MAX,
    MIN_UPLOAD_MINUTES_MIN,
    POLL_SECONDS_MAX,
    POLL_SECONDS_MIN,
    SCENE_HOLD_SECONDS_MAX,
    SCENE_HOLD_SECONDS_MIN,
    account_config_from_ref,
    load_config,
    save_config,
)
from quotadeck.core.flashbudget import FlashBudget
from quotadeck.core.models import DisplayMode, UsageSnapshot
from quotadeck.core.scheduler import QuotaDeckRuntime, UploadResult
from quotadeck.core.severity import worst_severity
from quotadeck.devices.aula_f108.device import aula_software_running, enumerate_interfaces, wired_mode_ok
from quotadeck.discovery.accounts import discover_accounts

APP_QSS = """
QMainWindow, QWidget { background: #101418; color: #E8F0F4; font-size: 13px; }
QLabel { color: #E8F0F4; }
QLineEdit, QComboBox {
    background: #1A242C; color: #E8F0F4; border: 1px solid #2A3238; padding: 4px 6px;
}
QPushButton {
    background: #1A242C; color: #E8F0F4; border: 1px solid #2A3238; padding: 7px 12px;
}
QPushButton:hover { border-color: #3DDC97; }
QPushButton:disabled { color: #5A6870; border-color: #2A3238; }
QPushButton#primary { background: #16332B; border-color: #3DDC97; color: #3DDC97; }
QPushButton#primary:disabled { color: #3A5A50; border-color: #2A4038; }
QPushButton#lang { min-width: 44px; padding: 6px 10px; }
QListWidget { background: #141A1E; border: 1px solid #2A3238; }
QCheckBox { color: #E8F0F4; }
"""


class AccountRow(QWidget):
    def __init__(self, account: AccountConfig) -> None:
        super().__init__()
        self.account = account
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        self.check = QCheckBox(account.provider.upper())
        self.check.setChecked(account.enabled)
        self.check.setMinimumWidth(78)
        self.source = QLabel(account.source_kind.upper() or "CLI")
        self.source.setFixedWidth(36)
        self.source.setStyleSheet("color:#8CB4B0;")
        self.alias = QLineEdit(account.alias)
        self.alias.setMaxLength(10)
        self.alias.setFixedWidth(92)
        self.remaining = QLabel("--%")
        self.remaining.setFixedWidth(48)
        self.remaining.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.meta = QLabel((account.source_label or "").upper())
        self.meta.setStyleSheet("color:#7A9094;")
        layout.addWidget(self.check)
        layout.addWidget(self.source)
        layout.addWidget(self.alias)
        layout.addWidget(self.remaining)
        layout.addWidget(self.meta, 1)

    def to_config(self) -> AccountConfig:
        return AccountConfig(
            provider=self.account.provider,
            account_id=self.account.account_id,
            alias=self.alias.text().strip().upper() or self.account.alias,
            enabled=self.check.isChecked(),
            source_path=self.account.source_path,
            source_kind=self.account.source_kind,
            source_label=self.account.source_label,
        )

    def set_usage(self, snapshot: UsageSnapshot | None) -> None:
        if snapshot is None:
            self.remaining.setText("--%")
            return
        remaining = snapshot.critical_remaining
        if remaining is None:
            self.remaining.setText(snapshot.status.upper()[:5])
            self.remaining.setStyleSheet("color:#F0C440;")
            return
        value = int(round(remaining))
        self.remaining.setText(f"{value}%")
        if value <= 10:
            self.remaining.setStyleSheet("color:#FF783C;")
        elif value <= 20:
            self.remaining.setStyleSheet("color:#F0C440;")
        else:
            self.remaining.setStyleSheet("color:#3DDC97;")
        extra = " · ".join(
            f"{w.label} {int(round(w.remaining_percent))}%" for w in snapshot.windows[:2]
        )
        if not extra:
            extra = snapshot.plan or self.account.source_label
        self.meta.setText((extra or "").upper())


class MainWindow(QMainWindow):
    def __init__(self, *, live: bool = True) -> None:
        super().__init__()
        self.resize(920, 620)
        self.config = load_config()
        self.runtime = QuotaDeckRuntime(self.config)
        self.controller = RefreshController(self.runtime, self)
        self.controller.polled.connect(self._on_polled)
        self.controller.upload_done.connect(self._on_upload_done)
        self.controller.poll_failed.connect(self._on_poll_failed)
        self.controller.schedule_updated.connect(self._on_schedule)
        self.controller.busy_changed.connect(self.set_busy)
        self.rows: list[AccountRow] = []
        self._busy = False
        self.tray_show = None
        self.tray_refresh = None
        self.tray_upload = None
        self.tray_quit = None
        self.tray = attach_tray(self) if live else None

        root = QWidget()
        layout = QHBoxLayout(root)
        left = QVBoxLayout()
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size:16px; font-weight:600;")
        self.lang_btn = QPushButton()
        self.lang_btn.setObjectName("lang")
        self.lang_btn.clicked.connect(self.toggle_language)
        header.addWidget(self.title, 1)
        header.addWidget(self.lang_btn)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#8CB4B0;")
        left.addLayout(header)
        left.addWidget(self.hint)
        self.list = QListWidget()
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        left.addWidget(self.list)

        form = QFormLayout()
        self.mode = QComboBox()
        self.poll = Stepper(
            minimum=POLL_SECONDS_MIN,
            maximum=POLL_SECONDS_MAX,
            value=self.config.poll_seconds,
        )
        self.poll.setToolTip("")
        self.hold = Stepper(
            minimum=SCENE_HOLD_SECONDS_MIN,
            maximum=SCENE_HOLD_SECONDS_MAX,
            value=self.config.scene_hold_seconds,
        )
        self.min_up = Stepper(
            minimum=MIN_UPLOAD_MINUTES_MIN,
            maximum=MIN_UPLOAD_MINUTES_MAX,
            value=self.config.min_upload_minutes,
        )
        self.min_up.valueChanged.connect(self.update_flash_label)
        self.poll.valueChanged.connect(self.update_flash_label)
        self.startup = QCheckBox()
        self.startup.setChecked(self.config.launch_at_startup)
        self.lbl_mode = QLabel()
        self.lbl_poll = QLabel()
        self.lbl_hold = QLabel()
        self.lbl_upload = QLabel()
        form.addRow(self.lbl_mode, self.mode)
        form.addRow(self.lbl_poll, self.poll)
        form.addRow(self.lbl_hold, self.hold)
        form.addRow(self.lbl_upload, self.min_up)
        form.addRow(self.startup)
        self.timing_hint = QLabel()
        self.timing_hint.setWordWrap(True)
        self.timing_hint.setStyleSheet("color:#8CB4B0;")
        left.addLayout(form)
        left.addWidget(self.timing_hint)

        buttons = QHBoxLayout()
        self.detect_btn = QPushButton()
        self.detect_btn.clicked.connect(self.detect)
        self.preview_btn = QPushButton()
        self.preview_btn.clicked.connect(self.refresh_preview)
        self.apply_btn = QPushButton()
        self.apply_btn.clicked.connect(self.apply)
        self.upload_btn = QPushButton()
        self.upload_btn.setObjectName("primary")
        self.upload_btn.clicked.connect(self.upload_now)
        buttons.addWidget(self.detect_btn)
        buttons.addWidget(self.preview_btn)
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.upload_btn)
        left.addLayout(buttons)

        right = QVBoxLayout()
        self.preview_title = QLabel()
        self.keyboard = QLabel("")
        self.preview = LcdPreview(3)
        self.status = QLabel()
        self.sched = QLabel("")
        self.sched.setStyleSheet("color:#8CB4B0;")
        self.flash = QLabel("")
        self.flash.setWordWrap(True)
        self.flash.setStyleSheet("color:#8CB4B0;")
        self.missing = QLabel("")
        self.missing.setWordWrap(True)
        self.missing.setStyleSheet("color:#8CB4B0;")
        right.addWidget(self.preview_title)
        right.addWidget(self.keyboard)
        right.addWidget(self.preview)
        right.addWidget(self.status)
        right.addWidget(self.sched)
        right.addWidget(self.flash)
        right.addWidget(self.missing)
        right.addStretch()

        layout.addLayout(left, 3)
        layout.addLayout(right, 2)
        self.setCentralWidget(root)
        self.reload_accounts()
        self.apply_language()
        if live:
            self.controller.start()

    def _lang(self) -> str:
        return "en" if self.config.ui_language == "en" else "ko"

    def _t(self, key: str, **kwargs: object) -> str:
        return tr(self._lang(), key, **kwargs)

    def apply_language(self) -> None:
        current = self.mode.currentData() or self.config.display_mode.value
        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItem(self._t("mode_smart"), "smart")
        self.mode.addItem(self._t("mode_fixed"), "fixed")
        index = self.mode.findData(current)
        self.mode.setCurrentIndex(index if index >= 0 else 0)
        self.mode.blockSignals(False)
        self.setWindowTitle(self._t("window_title"))
        self.title.setText(self._t("accounts_title"))
        self.hint.setText(self._t("accounts_hint"))
        self.lbl_mode.setText(self._t("display_mode"))
        self.lbl_poll.setText(self._t("poll"))
        self.lbl_hold.setText(self._t("hold"))
        self.lbl_upload.setText(self._t("min_upload"))
        self.poll.setSuffix(self._t("suffix_sec"))
        self.hold.setSuffix(self._t("suffix_sec"))
        self.min_up.setSuffix(self._t("suffix_min"))
        self.poll.setToolTip(self._t("poll_tip"))
        self.hold.setToolTip(self._t("hold_tip"))
        self.min_up.setToolTip(self._t("upload_tip"))
        self.timing_hint.setText(self._t("timing_hint"))
        self.startup.setText(self._t("startup"))
        self.detect_btn.setText(self._t("detect"))
        self.preview_btn.setText(self._t("preview"))
        self.apply_btn.setText(self._t("save"))
        self.upload_btn.setText(self._t("upload"))
        self.preview_title.setText(self._t("preview_title"))
        self.lang_btn.setText(self._t("lang_button"))
        self.lang_btn.setToolTip(self._t("lang_tip"))
        if self.status.text() in {"", "준비됨", "Ready"}:
            self.status.setText(self._t("ready"))
        if self.tray_show is not None:
            self.tray_show.setText(self._t("tray_open"))
            self.tray_refresh.setText(self._t("tray_refresh"))
            self.tray_upload.setText(self._t("tray_upload"))
            self.tray_quit.setText(self._t("tray_quit"))
        self.update_flash_label()
        self.update_keyboard_status()
        self.update_missing_label()

    def toggle_language(self) -> None:
        # Only the language changes here; other pending edits are saved by
        # the explicit Save action so saved values and runtime never diverge.
        self.config.ui_language = "en" if self._lang() == "ko" else "ko"
        save_config(self.config)
        self.apply_language()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        enabled = not busy
        for button in (self.detect_btn, self.preview_btn, self.apply_btn, self.upload_btn):
            button.setEnabled(enabled)
        if self.tray_upload is not None:
            self.tray_upload.setEnabled(enabled)
        if self.tray_refresh is not None:
            self.tray_refresh.setEnabled(enabled)

    def reload_accounts(self) -> None:
        self.list.clear()
        self.rows = []
        accounts = self.config.accounts or [account_config_from_ref(item) for item in discover_accounts()]
        if not self.config.accounts:
            self.config.accounts = accounts
        for account in accounts:
            item = QListWidgetItem()
            row = AccountRow(account)
            item.setSizeHint(row.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, account)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self.rows.append(row)
        self.update_missing_label()

    def collect_config(self) -> AppConfig:
        accounts: list[AccountConfig] = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            widget = self.list.itemWidget(item)
            if isinstance(widget, AccountRow):
                accounts.append(widget.to_config())
                continue
            base: AccountConfig = item.data(Qt.ItemDataRole.UserRole)
            accounts.append(base)
        mode = self.mode.currentData() or self.mode.currentText()
        return AppConfig(
            accounts=accounts,
            display_mode=DisplayMode(str(mode)),
            theme=self.config.theme,
            poll_seconds=self.poll.value(),
            scene_hold_seconds=self.hold.value(),
            min_upload_minutes=self.min_up.value(),
            max_age_minutes=self.config.max_age_minutes,
            daily_flash_limit=self.config.daily_flash_limit,
            frame_budget=self.config.frame_budget,
            launch_at_startup=self.startup.isChecked(),
            ui_language=self._lang(),
        )

    def detect(self) -> None:
        if self._busy:
            return
        found = discover_accounts()
        previous = {(item.provider, item.account_id): item for item in self.collect_config().accounts}
        merged: list[AccountConfig] = []
        for account in found:
            prev = previous.get((account.provider, account.account_id))
            merged.append(
                account_config_from_ref(
                    account,
                    enabled=prev.enabled if prev else True,
                    alias=prev.alias if prev else account.display_name,
                )
            )
        self.config.accounts = merged
        self.reload_accounts()
        self.status.setText(self._t("status_found", count=len(found)))
        self.refresh_preview()

    def _validate_timing_inputs(self) -> bool:
        fields = (
            (self.poll, "poll", "suffix_sec"),
            (self.hold, "hold", "suffix_sec"),
            (self.min_up, "min_upload", "suffix_min"),
        )
        for control, label_key, suffix_key in fields:
            if control.commit():
                continue
            control.display.setFocus()
            control.display.selectAll()
            QMessageBox.warning(
                self,
                self._t("validation_title"),
                self._t(
                    "invalid_timing",
                    field=self._t(label_key),
                    minimum=control.minimum(),
                    maximum=control.maximum(),
                    unit=self._t(suffix_key).strip(),
                ),
            )
            return False
        return True

    def apply(self) -> bool:
        # Save settings and hand them to the long-lived runtime; never rebuild
        # the runtime, so flash budget and upload history are preserved.
        if not self._validate_timing_inputs():
            return False
        self.config = self.collect_config()
        save_config(self.config)
        set_launch_at_startup(self.config.launch_at_startup)
        self.controller.update_config(self.config)
        self.update_flash_label()
        self.status.setText(self._t("status_saved"))
        return True

    def refresh_preview(self) -> None:
        if not self.apply():
            return
        self.status.setText(self._t("status_reading"))
        self.controller.request_refresh()

    def refresh_from_tray(self) -> None:
        # Tray action: read the latest usage without saving pending edits and
        # without forcing an LCD write.
        self.controller.request_refresh()

    def upload_now(self) -> None:
        if not self.apply():
            return
        self.update_keyboard_status()
        interfaces = enumerate_interfaces()
        if not wired_mode_ok(interfaces):
            QMessageBox.warning(self, "QuotaDeck", self._t("warn_usb"))
            return
        running = aula_software_running()
        if running:
            QMessageBox.warning(self, "QuotaDeck", self._t("warn_aula", names=", ".join(running)))
            return
        self.status.setText(self._t("status_uploading"))
        self.controller.request_upload()

    def _on_polled(self, snapshots: list[UsageSnapshot], frames) -> None:
        self._apply_snapshots(snapshots)
        if frames:
            self.preview.show_frames(frames)
        ok = sum(1 for snap in snapshots if snap.status == "ok")
        self.status.setText(self._t("status_poll", ok=ok, total=len(snapshots)))
        self._update_tray()

    def _on_upload_done(self, result: UploadResult, manual: bool) -> None:
        self.status.setText(self._upload_text(result))
        self._update_tray()
        if manual and result.code == "device_error":
            QMessageBox.warning(self, "QuotaDeck", result.detail)

    def _on_poll_failed(self, message: str, manual: bool) -> None:
        # Automatic refreshes must never spam modal popups from the tray.
        self.status.setText(self._t("status_poll_failed", error=message))
        if manual:
            QMessageBox.warning(self, "QuotaDeck", message)

    def _on_schedule(self, last_poll: datetime | None, next_poll: datetime | None) -> None:
        self.sched.setText(
            self._t(
                "sched_line",
                last=_fmt_time(last_poll),
                next=_fmt_time(next_poll),
            )
        )
        self._update_tray()

    def _upload_text(self, result: UploadResult) -> str:
        key = {
            "uploaded": "up_uploaded",
            "preview": "up_uploaded",
            "unchanged": "up_unchanged",
            "cooldown": "up_cooldown",
            "daily_limit": "up_daily",
            "device_offline": "up_offline",
            "device_busy": "up_busy",
            "no_accounts": "up_no_accounts",
            "device_error": "up_error",
        }.get(result.code, "up_error")
        return self._t(
            key,
            frames=result.frames,
            detail=result.detail,
            next=_fmt_time(result.next_allowed),
        )

    def _update_tray(self) -> None:
        if self.tray is None:
            return
        worst = worst_severity(self.runtime.state.severity.values())
        self.tray.setIcon(severity_icon(worst))
        lines = ["QuotaDeck"]
        if self.controller.last_poll is not None:
            lines.append(self._t("tip_last_poll", time=_fmt_time(self.controller.last_poll)))
        if self.controller.next_poll is not None:
            lines.append(self._t("tip_next_poll", time=_fmt_time(self.controller.next_poll)))
        if self.runtime.budget.last_upload is not None:
            lines.append(self._t("tip_last_upload", time=_fmt_time(self.runtime.budget.last_upload)))
        self.tray.setToolTip("\n".join(lines))

    def _apply_snapshots(self, snapshots: list[UsageSnapshot]) -> None:
        by_key = {item.key: item for item in snapshots}
        for row in self.rows:
            row.set_usage(by_key.get(f"{row.account.provider}:{row.account.account_id}"))

    def update_flash_label(self) -> None:
        budget = FlashBudget(min_interval=timedelta(minutes=self.min_up.value()))
        daily = budget.estimated_daily_writes(self.poll.value())
        years = budget.estimated_years(self.poll.value())
        self.flash.setText(self._t("flash", daily=daily, years=years))

    def update_keyboard_status(self) -> None:
        interfaces = enumerate_interfaces()
        running = aula_software_running()
        if wired_mode_ok(interfaces):
            extra = self._t("kb_aula", names=", ".join(running)) if running else ""
            self.keyboard.setText(self._t("kb_wired", extra=extra))
            self.keyboard.setStyleSheet("color:#3DDC97;")
        elif interfaces:
            self.keyboard.setText(self._t("kb_seen"))
            self.keyboard.setStyleSheet("color:#F0C440;")
        else:
            self.keyboard.setText(self._t("kb_missing"))
            self.keyboard.setStyleSheet("color:#FF783C;")

    def update_missing_label(self) -> None:
        present = {item.provider for item in self.config.accounts}
        missing = [name for name in ("codex", "cursor", "claude", "grok") if name not in present]
        if not missing:
            self.missing.setText(self._t("found_all"))
            return
        hints = {
            "codex": self._t("hint_codex"),
            "cursor": self._t("hint_cursor"),
            "claude": self._t("hint_claude"),
            "grok": self._t("hint_grok"),
        }
        self.missing.setText(
            self._t(
                "missing",
                names=", ".join(name.upper() for name in missing),
                hints=" · ".join(hints[name] for name in missing),
            )
        )

    def close_app(self) -> None:
        self.controller.shutdown()
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()


def _fmt_time(when: datetime | None) -> str:
    if when is None:
        return "--:--"
    return when.astimezone().strftime("%H:%M:%S")


def run_app() -> int:
    from quotadeck.core.logs import setup_logging

    setup_logging()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    return app.exec()
