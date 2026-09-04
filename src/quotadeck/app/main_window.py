from __future__ import annotations

import sys
from datetime import timedelta

from PySide6.QtCore import Qt, QThread, Signal
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from quotadeck.config import AccountConfig, AppConfig, account_config_from_ref, load_config, save_config
from quotadeck.core.flashbudget import FlashBudget
from quotadeck.core.models import DisplayMode, Severity, UsageSnapshot
from quotadeck.core.scheduler import QuotaDeckRuntime
from quotadeck.devices.aula_f108.device import aula_software_running, enumerate_interfaces, wired_mode_ok
from quotadeck.discovery.accounts import discover_accounts
from quotadeck.app.preview_widget import LcdPreview
from quotadeck.app.startup import set_launch_at_startup
from quotadeck.app.tray import attach_tray, severity_icon

APP_QSS = """
QMainWindow, QWidget { background: #101418; color: #E8F0F4; font-size: 13px; }
QLabel { color: #E8F0F4; }
QLineEdit, QSpinBox, QComboBox {
    background: #1A242C; color: #E8F0F4; border: 1px solid #2A3238; padding: 4px 6px;
}
QPushButton {
    background: #1A242C; color: #E8F0F4; border: 1px solid #2A3238; padding: 7px 12px;
}
QPushButton:hover { border-color: #3DDC97; }
QPushButton#primary { background: #16332B; border-color: #3DDC97; color: #3DDC97; }
QListWidget { background: #141A1E; border: 1px solid #2A3238; }
QCheckBox { color: #E8F0F4; }
"""


class Worker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, runtime: QuotaDeckRuntime, force: bool = True) -> None:
        super().__init__()
        self.runtime = runtime
        self.force = force

    def run(self) -> None:
        try:
            self.done.emit(self.runtime.tick(force=self.force))
        except Exception as exc:
            self.failed.emit(str(exc))


class PreviewWorker(QThread):
    done = Signal(object, object)
    failed = Signal(str)

    def __init__(self, runtime: QuotaDeckRuntime) -> None:
        super().__init__()
        self.runtime = runtime

    def run(self) -> None:
        try:
            snapshots = self.runtime.poll()
            frames = self.runtime.build_frames(snapshots)
            self.done.emit(snapshots, frames)
        except Exception as exc:
            self.failed.emit(str(exc))


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
        extra = snapshot.plan or self.account.source_label
        self.meta.setText((extra or "").upper())


class MainWindow(QMainWindow):
    def __init__(self, *, live: bool = True) -> None:
        super().__init__()
        self.setWindowTitle("QuotaDeck — F108 Pro Rate Limit")
        self.resize(920, 600)
        self.config = load_config()
        self.runtime = QuotaDeckRuntime(self.config)
        self.worker: Worker | None = None
        self.preview_worker: PreviewWorker | None = None
        self.rows: list[AccountRow] = []
        self.tray = attach_tray(self, self.runtime) if live else None

        root = QWidget()
        layout = QHBoxLayout(root)
        left = QVBoxLayout()
        title = QLabel("키보드에 표시할 계정")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        hint = QLabel("이 PC에 로그인된 CLI / 앱 계정을 찾고, 체크한 계정만 F108 Pro LCD에 올립니다.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8CB4B0;")
        left.addWidget(title)
        left.addWidget(hint)
        self.list = QListWidget()
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        left.addWidget(self.list)

        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(["smart", "fixed"])
        self.mode.setCurrentText(self.config.display_mode.value)
        self.poll = QSpinBox()
        self.poll.setRange(15, 600)
        self.poll.setValue(self.config.poll_seconds)
        self.min_up = QSpinBox()
        self.min_up.setRange(1, 120)
        self.min_up.setValue(self.config.min_upload_minutes)
        self.startup = QCheckBox("Windows 시작 시 실행")
        self.startup.setChecked(self.config.launch_at_startup)
        form.addRow("표시 모드", self.mode)
        form.addRow("폴링(초)", self.poll)
        form.addRow("최소 업로드(분)", self.min_up)
        form.addRow(self.startup)
        left.addLayout(form)

        buttons = QHBoxLayout()
        detect_btn = QPushButton("계정 다시 찾기")
        detect_btn.clicked.connect(self.detect)
        preview_btn = QPushButton("미리보기")
        preview_btn.clicked.connect(self.refresh_preview)
        apply_btn = QPushButton("저장")
        apply_btn.clicked.connect(self.apply)
        upload_btn = QPushButton("지금 키보드에 올리기")
        upload_btn.setObjectName("primary")
        upload_btn.clicked.connect(self.upload_now)
        buttons.addWidget(detect_btn)
        buttons.addWidget(preview_btn)
        buttons.addWidget(apply_btn)
        buttons.addWidget(upload_btn)
        left.addLayout(buttons)

        right = QVBoxLayout()
        self.keyboard = QLabel("")
        self.preview = LcdPreview(3)
        self.status = QLabel("준비됨")
        self.flash = QLabel("")
        self.missing = QLabel("")
        self.missing.setWordWrap(True)
        self.missing.setStyleSheet("color:#8CB4B0;")
        right.addWidget(QLabel("F108 Pro 미리보기"))
        right.addWidget(self.keyboard)
        right.addWidget(self.preview)
        right.addWidget(self.status)
        right.addWidget(self.flash)
        right.addWidget(self.missing)
        right.addStretch()

        layout.addLayout(left, 3)
        layout.addLayout(right, 2)
        self.setCentralWidget(root)
        self.reload_accounts()
        self.update_flash_label()
        self.update_keyboard_status()
        if live:
            self.refresh_preview()

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
        return AppConfig(
            accounts=accounts,
            display_mode=DisplayMode(self.mode.currentText()),
            theme=self.config.theme,
            poll_seconds=self.poll.value(),
            min_upload_minutes=self.min_up.value(),
            max_age_minutes=self.config.max_age_minutes,
            daily_flash_limit=self.config.daily_flash_limit,
            frame_budget=self.config.frame_budget,
            launch_at_startup=self.startup.isChecked(),
        )

    def detect(self) -> None:
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
        self.status.setText(f"계정 {len(found)}개 발견")
        self.refresh_preview()

    def apply(self) -> None:
        self.config = self.collect_config()
        save_config(self.config)
        set_launch_at_startup(self.config.launch_at_startup)
        self.runtime = QuotaDeckRuntime(self.config)
        self.update_flash_label()
        self.status.setText("설정을 저장했습니다")

    def refresh_preview(self) -> None:
        self.apply()
        self.status.setText("사용량을 읽는 중…")
        self.preview_worker = PreviewWorker(self.runtime)
        self.preview_worker.done.connect(self._on_preview)
        self.preview_worker.failed.connect(self._on_fail)
        self.preview_worker.start()

    def upload_now(self) -> None:
        self.apply()
        self.update_keyboard_status()
        if "미연결" in self.keyboard.text():
            QMessageBox.warning(self, "QuotaDeck", "F108 Pro가 USB-C 유선(Fn+4)으로 연결되어 있는지 확인하세요.")
            return
        running = aula_software_running()
        if running:
            QMessageBox.warning(self, "QuotaDeck", f"공식 AULA 프로그램을 먼저 종료하세요: {', '.join(running)}")
            return
        self.status.setText("키보드에 올리는 중…")
        self.worker = Worker(self.runtime, force=True)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()

    def _on_preview(self, snapshots, frames) -> None:
        self._apply_snapshots(snapshots)
        if frames:
            self.preview.show_image(frames[0].image)
        count = len(snapshots)
        enabled = sum(1 for item in self.config.accounts if item.enabled)
        self.status.setText(f"미리보기 준비 · 선택 {enabled}개 · 조회 {count}개")

    def _on_done(self, message: str) -> None:
        self.status.setText(message)
        frames = self.runtime.state.last_frames
        if frames:
            self.preview.show_image(frames[0].image)
        self._apply_snapshots(list(self.runtime.state.previous.values()))
        worst = None
        if self.runtime.state.severity:
            worst = min(self.runtime.state.severity.values(), key=lambda item: list(Severity).index(item))
        if self.tray is not None:
            self.tray.setIcon(severity_icon(worst))
            uploaded = self.runtime.budget.last_upload
            if uploaded:
                self.tray.setToolTip(f"QuotaDeck — last upload {uploaded.astimezone():%H:%M}")

    def _apply_snapshots(self, snapshots: list[UsageSnapshot]) -> None:
        by_key = {item.key: item for item in snapshots}
        for row in self.rows:
            row.set_usage(by_key.get(f"{row.account.provider}:{row.account.account_id}"))

    def _on_fail(self, message: str) -> None:
        QMessageBox.warning(self, "QuotaDeck", message)
        self.status.setText(message)

    def update_flash_label(self) -> None:
        budget = FlashBudget(min_interval=timedelta(minutes=self.min_up.value()))
        self.flash.setText(f"예상 하루 플래시 기록: 약 {budget.estimated_daily_writes(self.poll.value())}회")

    def update_keyboard_status(self) -> None:
        interfaces = enumerate_interfaces()
        running = aula_software_running()
        if wired_mode_ok(interfaces):
            extra = f" · AULA 프로그램 실행 중({', '.join(running)})" if running else ""
            self.keyboard.setText(f"키보드: 연결됨 (USB-C){extra}")
            self.keyboard.setStyleSheet("color:#3DDC97;")
        elif interfaces:
            self.keyboard.setText("키보드: 보임 · USB-C 유선(Fn+4)으로 바꾸세요")
            self.keyboard.setStyleSheet("color:#F0C440;")
        else:
            self.keyboard.setText("키보드: 미연결 · USB-C를 꽂고 Fn+4를 누르세요")
            self.keyboard.setStyleSheet("color:#FF783C;")

    def update_missing_label(self) -> None:
        present = {item.provider for item in self.config.accounts}
        missing = [name for name in ("codex", "cursor", "claude", "grok") if name not in present]
        if not missing:
            self.missing.setText("Codex / Cursor / Claude / Grok 계정을 모두 찾았습니다.")
            return
        hints = {
            "codex": "Codex CLI 로그인",
            "cursor": "Cursor 앱 또는 Cursor CLI 로그인",
            "claude": "Claude Code 로그인",
            "grok": "Grok Build CLI 로그인",
        }
        lines = " · ".join(hints[name] for name in missing)
        self.missing.setText(f"아직 없는 제공자: {', '.join(name.upper() for name in missing)}\n{lines}")

    def close_app(self) -> None:
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    return app.exec()
