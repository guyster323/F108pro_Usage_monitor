from __future__ import annotations

import logging
import sys
import time
from datetime import timedelta

from PySide6.QtCore import QSize, QThread, QTimer, Qt, QUrl, Signal, Slot, qVersion
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from quotadeck import __version__
from quotadeck.app.hint import HintButton
from quotadeck.app.i18n import tr
from quotadeck.app.icons import (
    ICON_BRAIN,
    ICON_CLOCK,
    ICON_EXCHANGE,
    ICON_INFO,
    ICON_KEYBOARD,
    ICON_LOCK,
    ICON_PIN,
    glyph_icon,
)
from quotadeck.app.preview_widget import LcdPreview
from quotadeck.app.startup import set_launch_at_startup
from quotadeck.app.stepper import Stepper
from quotadeck.app.tray import attach_tray, severity_icon
from quotadeck.config import (
    MAX_USD_TO_KRW_RATE,
    MIN_USD_TO_KRW_RATE,
    AccountConfig,
    AppConfig,
    CursorUsageBinding,
    account_config_from_ref,
    load_config,
    save_config,
    upsert_cursor_binding,
)
from quotadeck.core.flashbudget import ACTIVE_HOURS_PER_DAY, FlashBudget
from quotadeck.core.mask import mask_text, safe_display_text
from quotadeck.core.models import DisplayMode, MetricMode, Severity, UsageSnapshot
from quotadeck.core.scheduler import QuotaDeckRuntime
from quotadeck.usage.fx import FxFallback, FxRateQuote
from quotadeck.core.severity import worst_severity
from quotadeck.diagnostics import DiagnosticSession, configure_diagnostics, diagnostic_log_directory
from quotadeck.devices.aula_f108.device import aula_software_running, enumerate_interfaces, wired_mode_ok
from quotadeck.discovery.accounts import discover_accounts
from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET, LCD_MAX_FRAMES
from quotadeck.devices.aula_f108.payload import payload_padded_size
from quotadeck.renderer.budget import (
    SceneBudgetError,
    enabled_account_count,
    playlist_budget_kwargs,
    required_playlist_frames,
)
from quotadeck.renderer.layout import compact_cost_pair, compact_token_count
from quotadeck.usage.display import CumulativeSnapshot
from quotadeck.usage.models import CostCurrency, UsagePeriod

log = logging.getLogger("quotadeck.ui")

CURSOR_ANALYTICS_URL = "https://cursor.com/dashboard/analytics"
CURSOR_API_DOCS_URL = "https://cursor.com/docs/api"
CURSOR_ADMIN_API_DOCS_URL = "https://cursor.com/docs/account/teams/admin-api"


def open_official_url(url: str) -> bool:
    """Open a documented official URL with the OS handler."""
    return bool(QDesktopServices.openUrl(QUrl(url)))


def show_cursor_guide_dialog(
    parent,
    lang: str,
    *,
    title_key: str,
    body_key: str,
    links: tuple[tuple[str, str], ...],
    continue_key: str,
    cancel_key: str,
) -> bool:
    """Show one onboarding modal. True means continue; cancel does not mutate config."""

    box = QMessageBox(parent)
    box.setWindowTitle(tr(lang, title_key))
    box.setText(tr(lang, body_key))
    box.setIcon(QMessageBox.Icon.Information)
    box.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByKeyboard
        | Qt.TextInteractionFlag.TextSelectableByMouse
    )
    link_buttons: list[tuple[object, str]] = []
    for label_key, url in links:
        button = box.addButton(tr(lang, label_key), QMessageBox.ButtonRole.ActionRole)
        button.setAccessibleName(tr(lang, label_key))
        button.setAutoDefault(False)
        link_buttons.append((button, url))
    continue_btn = box.addButton(
        tr(lang, continue_key), QMessageBox.ButtonRole.AcceptRole
    )
    continue_btn.setAccessibleName(tr(lang, continue_key))
    cancel_btn = box.addButton(tr(lang, cancel_key), QMessageBox.ButtonRole.RejectRole)
    cancel_btn.setAccessibleName(tr(lang, cancel_key))
    box.setDefaultButton(continue_btn)
    box.setEscapeButton(cancel_btn)
    while True:
        box.exec()
        clicked = box.clickedButton()
        for button, url in link_buttons:
            if clicked is button:
                open_official_url(url)
                break
        else:
            return clicked is continue_btn


def _price_catalog_date() -> str:
    """Keep the quota UI usable while an optional pricing catalog is absent."""

    try:
        from quotadeck.usage.pricing import PRICE_CATALOG_AS_OF
    except (ImportError, AttributeError):
        return "N/A"
    return PRICE_CATALOG_AS_OF.isoformat()


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
QToolButton#hint {
    background: #1A242C; color: #E8F0F4; border: 1px solid #2A3238; padding: 2px;
}
QToolButton#hint:hover, QToolButton#hint:focus { border-color: #3DDC97; }
QToolButton#cursorSource {
    background: #1A242C; color: #E8F0F4; border: 1px solid #2A3238; padding: 2px 8px;
}
QToolButton#cursorSource:hover, QToolButton#cursorSource:focus { border-color: #3DDC97; }
QToolButton#cursorSource:disabled { color: #5A6870; border-color: #2A3238; }
QListWidget { background: #141A1E; border: 1px solid #2A3238; }
QCheckBox { color: #E8F0F4; }
QScrollArea { border: none; background: #101418; }
"""


def cycle_seconds(account_count: int, hold_seconds: int) -> int:
    return max(0, int(account_count)) * max(0, int(hold_seconds))


def playlist_budget_message(lang: str, error: SceneBudgetError) -> str:
    return tr(lang, "playlist_budget_impossible", **playlist_budget_kwargs(error))


def _worker_failure_text(exc: BaseException, lang: str) -> str:
    if isinstance(exc, SceneBudgetError):
        return playlist_budget_message(lang, exc)
    return mask_text(str(exc))


def _wrap_with_hint(control: QWidget, hint: HintButton) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(control, 1)
    layout.addWidget(hint)
    return row

class Worker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, runtime: QuotaDeckRuntime, force: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.force = force
        self.setObjectName("keyboard-upload")

    def run(self) -> None:
        started = time.monotonic()
        log.info("event=worker_run_start kind=upload force=%s", self.force)
        try:
            result = self.runtime.tick(force=self.force)
            if not self.runtime.state.last_frames:
                # A flash cooldown can reject the write before maybe_upload()
                # renders anything. The settings window still needs an initial
                # preview from the snapshots that were just polled.
                self.runtime.state.last_frames = self.runtime.build_frames(
                    list(self.runtime.state.last_polled)
                )
            log.info(
                "event=worker_result kind=upload duration_ms=%d result=%r",
                int((time.monotonic() - started) * 1000),
                result,
            )
            self.done.emit(result)
        except Exception as exc:
            log.exception(
                "event=worker_exception kind=upload duration_ms=%d",
                int((time.monotonic() - started) * 1000),
            )
            lang = getattr(getattr(self.runtime, "config", None), "ui_language", "ko")
            self.failed.emit(_worker_failure_text(exc, lang))


class PreviewWorker(QThread):
    done = Signal(object, object)
    failed = Signal(str)
    def __init__(self, runtime: QuotaDeckRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setObjectName("usage-preview")

    def run(self) -> None:
        started = time.monotonic()
        log.info("event=worker_run_start kind=preview")
        try:
            snapshots = self.runtime.poll()
            frames = self.runtime.build_frames(snapshots)
            log.info(
                "event=worker_result kind=preview duration_ms=%d accounts=%d frames=%d",
                int((time.monotonic() - started) * 1000),
                len(snapshots),
                len(frames),
            )
            self.done.emit(snapshots, frames)
        except Exception as exc:
            log.exception(
                "event=worker_exception kind=preview duration_ms=%d",
                int((time.monotonic() - started) * 1000),
            )
            lang = getattr(getattr(self.runtime, "config", None), "ui_language", "ko")
            self.failed.emit(_worker_failure_text(exc, lang))

class CursorAdminConnectWorker(QThread):
    succeeded = Signal(str, str)
    failed = Signal(str)

    def __init__(
        self,
        account_id: str,
        email: str,
        api_key: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.account_id = account_id
        self.email = email
        self._api_key = api_key
        self.setObjectName("cursor-admin-connect")

    def run(self) -> None:
        try:
            from quotadeck.usage.cursor_admin import (
                collect_cursor_admin,
                persist_admin_key_after_live_validation,
            )

            attempt = collect_cursor_admin(
                account=self.account_id,
                email=self.email,
                api_key=self._api_key,
                force=True,
            )
            if persist_admin_key_after_live_validation(attempt, self._api_key):
                self.succeeded.emit(self.account_id, self.email)
                return
            self.failed.emit(
                mask_text(attempt.reason or "Cursor Admin API validation failed.")
            )
        except Exception as exc:
            log.exception("event=cursor_admin_connect_failed")
            self.failed.emit(mask_text(str(exc)))
        finally:
            self._api_key = ""


class AccountRow(QWidget):
    connect_csv = Signal()
    update_csv = Signal()
    disconnect_usage = Signal()
    connect_admin = Signal()
    update_admin = Signal()

    def __init__(self, account: AccountConfig) -> None:
        super().__init__()
        self.account = account
        self._lang = "ko"
        self._source = ""
        self._has_display = False
        self._last_snapshot: UsageSnapshot | CumulativeSnapshot | None = None
        self._last_currency = CostCurrency.USD
        self._last_usd_to_krw_rate = 1400.0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        self.check = QCheckBox(account.provider.upper())
        self.check.setChecked(account.enabled)
        self.check.setMinimumWidth(78)
        self.source = QLabel(account.source_kind.upper() or "CLI")
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setFixedWidth(36)
        self.source.setStyleSheet("color:#8CB4B0;")
        self.alias = QLineEdit(account.alias)
        self.alias.setMaxLength(10)
        self.alias.setFixedWidth(92)
        self.remaining = QLabel("--%")
        self.remaining.setTextFormat(Qt.TextFormat.PlainText)
        self.remaining.setFixedWidth(48)
        self.remaining.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.meta = QLabel((account.source_label or "").upper())
        self.meta.setTextFormat(Qt.TextFormat.PlainText)
        self.meta.setStyleSheet("color:#7A9094;")
        self.actions_btn = QToolButton()
        self.actions_btn.setObjectName("cursorSource")
        self.actions_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.actions_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.actions_btn.setFixedHeight(22)
        self.actions_btn.setMaximumWidth(88)
        self.actions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.actions_menu = QMenu(self.actions_btn)
        self.actions_btn.setMenu(self.actions_menu)
        layout.addWidget(self.check)
        layout.addWidget(self.source)
        layout.addWidget(self.alias)
        layout.addWidget(self.remaining)
        layout.addWidget(self.meta, 1)
        layout.addWidget(self.actions_btn)
        self._refresh_cursor_actions("")
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

    def apply_language(self, lang: str, *, source: str = "") -> None:
        self._lang = "en" if lang == "en" else "ko"
        self._refresh_cursor_actions(source)
        if self._has_display:
            self._render_snapshot()

    def set_actions_enabled(self, enabled: bool) -> None:
        self.actions_btn.setEnabled(enabled)

    def _add_source_action(self, key: str, callback) -> None:
        action = QAction(tr(self._lang, key), self.actions_menu)
        action.setToolTip(tr(self._lang, key))
        action.triggered.connect(callback)
        self.actions_menu.addAction(action)

    def _refresh_cursor_actions(self, source: str) -> None:
        self._source = source
        is_cursor = self.account.provider == "cursor"
        self.actions_btn.setVisible(is_cursor)
        if not is_cursor:
            return
        self.actions_menu.clear()
        if source == "csv":
            self.actions_btn.setText(tr(self._lang, "cursor_source_csv"))
            self._add_source_action("cursor_update_csv", self.update_csv.emit)
            self._add_source_action("cursor_switch_admin", self.connect_admin.emit)
            self._add_source_action("cursor_disconnect", self.disconnect_usage.emit)
            tip = tr(self._lang, "cursor_csv_tip")
        elif source == "admin_api":
            self.actions_btn.setText(tr(self._lang, "cursor_source_admin"))
            self._add_source_action("cursor_update_admin", self.update_admin.emit)
            self._add_source_action("cursor_switch_csv", self.connect_csv.emit)
            self._add_source_action("cursor_disconnect", self.disconnect_usage.emit)
            tip = tr(self._lang, "cursor_admin_tip")
        else:
            self.actions_btn.setText(tr(self._lang, "cursor_connect"))
            self._add_source_action("cursor_connect_csv", self.connect_csv.emit)
            self._add_source_action("cursor_connect_admin", self.connect_admin.emit)
            tip = tr(self._lang, "cursor_hint")
        self.actions_btn.setToolTip(tip)
        self.actions_btn.setStatusTip(tip)
        self.actions_btn.setAccessibleName(tr(self._lang, "cursor_actions"))
        self.actions_btn.setAccessibleDescription(tip)

    def _reset_display(self) -> None:
        self.remaining.setText("--%")
        self.remaining.setStyleSheet("")
        self.meta.setText((self.account.source_label or "").upper())
        self.meta.setStyleSheet("color:#7A9094;")
        self.meta.setToolTip("")
        self.setToolTip("")

    @staticmethod
    def _token_count(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        total = getattr(value, "total_tokens", value)
        try:
            parsed = int(round(total)) if isinstance(total, float) else int(total)
        except (TypeError, ValueError, OverflowError):
            return None
        return max(0, parsed)

    @classmethod
    def _current_tokens(cls, snapshot: CumulativeSnapshot) -> int | None:
        # New period-aware snapshots expose current_tokens. Keep today_tokens
        # as a compatibility path for cached/older snapshot implementations.
        for name in ("current_tokens", "this_tokens", "period_tokens", "today_tokens"):
            value = cls._token_count(getattr(snapshot, name, None))
            if value is not None:
                return value
        return None

    @classmethod
    def _average_tokens(cls, snapshot: CumulativeSnapshot) -> int | None:
        for name in ("average_tokens", "avg_tokens", "period_average_tokens"):
            value = cls._token_count(getattr(snapshot, name, None))
            if value is not None:
                return value
        report = snapshot.report
        comparison = getattr(report, "period_comparison", None)
        for name in ("average_tokens", "prior_average", "prior_daily_average"):
            value = cls._token_count(getattr(comparison, name, None))
            if value is not None:
                return value
        comparison = getattr(report, "today_comparison", None)
        return cls._token_count(getattr(comparison, "prior_daily_average", None))

    @classmethod
    def _model_breakdown(
        cls,
        snapshot: CumulativeSnapshot,
    ) -> list[tuple[str, int]]:
        """Return selected-period model counts for the desktop UI only."""

        candidates = None
        for name in (
            "current_model_totals",
            "period_model_totals",
            "current_models",
            "period_models",
        ):
            value = getattr(snapshot, name, None)
            if value is not None:
                candidates = value
                break

        report = snapshot.report
        if candidates is None:
            comparison = getattr(snapshot, "period_comparison", None)
            value = getattr(comparison, "current_models", None)
            if value is not None:
                candidates = value
        if candidates is None and report is not None:
            period = getattr(snapshot, "period", UsagePeriod.DAILY)
            period_value = getattr(period, "value", period)
            if str(period_value) == UsagePeriod.MONTHLY.value:
                end_day = report.end_day
                candidates = tuple(
                    model
                    for day in report.daily
                    if day.day.year == end_day.year and day.day.month == end_day.month
                    for model in day.models
                )
            else:
                candidates = report.today.models
            if not candidates:
                candidates = report.model_totals

        totals: dict[str, int] = {}
        for item in candidates or ():
            model = safe_display_text(getattr(item, "model", "unknown"))
            count = cls._token_count(getattr(item, "tokens", None))
            if count is not None:
                totals[model] = totals.get(model, 0) + count
        return sorted(totals.items(), key=lambda item: (-item[1], item[0].casefold()))

    def set_usage(self, snapshot: UsageSnapshot | None) -> None:
        self._render_usage(snapshot)

    def _render_usage(self, snapshot: UsageSnapshot | None) -> None:
        self._reset_display()
        if snapshot is None:
            return
        remaining = snapshot.critical_remaining
        if remaining is None:
            self.remaining.setText(snapshot.status.upper()[:5])
            self.remaining.setStyleSheet("color:#F0C440;")
            self.meta.setText(
                (snapshot.plan or self.account.source_label or "").upper()
            )
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
        if snapshot.error:
            reason = mask_text(snapshot.error)
            self.meta.setToolTip(reason)
            self.setToolTip(reason)

    def set_snapshot(
        self,
        snapshot: UsageSnapshot | CumulativeSnapshot | None,
        *,
        currency: CostCurrency = CostCurrency.USD,
        usd_to_krw_rate: float = 1400.0,
        krw_cost_label: str = "KRW (만원)",
    ) -> None:
        del krw_cost_label  # Language-aware label is resolved at render time.
        self._has_display = True
        self._last_snapshot = snapshot
        self._last_currency = currency
        self._last_usd_to_krw_rate = usd_to_krw_rate
        self._render_snapshot()

    def _render_snapshot(self) -> None:
        snapshot = self._last_snapshot
        currency = self._last_currency
        usd_to_krw_rate = self._last_usd_to_krw_rate
        krw_cost_label = tr(self._lang, "currency_krw")
        if not isinstance(snapshot, CumulativeSnapshot):
            self._render_usage(snapshot)
            return
        self._reset_display()
        if not snapshot.available:
            self.remaining.setText("N/A")
            self.remaining.setStyleSheet(
                "color:#FF783C;"
                if snapshot.severity is Severity.ERROR
                else "color:#F0C440;"
            )
            if snapshot.status == "unsupported" and self.account.provider == "cursor":
                detail = tr(self._lang, "cursor_need_connect")
            elif snapshot.status == "unsupported":
                detail = snapshot.source_label or "ADMIN API REQUIRED"
            elif snapshot.source_label and snapshot.source_label != "THIS DEVICE":
                detail = snapshot.source_label
            else:
                detail = "N/A"
            self.meta.setText(detail.upper())
            if snapshot.error:
                reason = mask_text(snapshot.error)
                self.meta.setToolTip(reason)
                self.setToolTip(reason)
            return
        current_tokens = self._current_tokens(snapshot)
        if current_tokens is None:
            self.remaining.setText("N/A")
            self.remaining.setStyleSheet("color:#F0C440;")
            return
        self.remaining.setText(compact_token_count(current_tokens))
        if snapshot.severity in {Severity.CRITICAL, Severity.EXHAUSTED}:
            self.remaining.setStyleSheet("color:#FF783C;")
        elif snapshot.severity in {Severity.CAUTION, Severity.STALE}:
            self.remaining.setStyleSheet("color:#F0C440;")
        else:
            self.remaining.setStyleSheet("color:#3DDC97;")
        average_tokens = self._average_tokens(snapshot)
        average = "N/A" if average_tokens is None else compact_token_count(average_tokens)
        visible = [f"{compact_token_count(current_tokens)} / {average}"]
        if snapshot.comparison_display:
            visible.append(snapshot.comparison_display)
        self.meta.setText(" · ".join(visible).upper())
        tooltip_lines = [
            f"THIS {compact_token_count(current_tokens)}",
            f"AVG {average} ({snapshot.comparison_display})",
        ]
        for key, kwargs in snapshot.average_gap_i18n_parts():
            tooltip_lines.append(tr(self._lang, key, **kwargs))
        selected = snapshot.period_comparison
        if (
            selected is not None
            and selected.history_estimated
            and average_tokens is not None
        ):
            tooltip_lines.append(
                "AVG EST: FIRST RETAINED PARTIAL MONTH PRORATED BY CALENDAR DAYS"
            )
        models = self._model_breakdown(snapshot)
        if models:
            tooltip_lines.append("MODEL USAGE — GUI ONLY")
            tooltip_lines.extend(
                f"{name}: {tokens:,} TOKENS" for name, tokens in models
            )
        current_cost, average_cost = compact_cost_pair(
            snapshot.this_cost_usd,
            snapshot.average_cost_usd,
            currency=currency,
            usd_to_krw_rate=usd_to_krw_rate,
        )
        if current_cost != "--" and average_cost != "--":
            currency_label = (
                krw_cost_label
                if currency is CostCurrency.KRW
                else currency.value.upper()
            )
            tooltip_lines.append(
                f"LIST {currency_label} THIS {current_cost} / AVG {average_cost}"
            )
        elif snapshot.cost_display:
            tooltip_lines.append(snapshot.cost_display)
        if snapshot.source_label:
            tooltip_lines.append(snapshot.source_label)
        if snapshot.error:
            tooltip_lines.append(mask_text(snapshot.error))
        tooltip = "\n".join(tooltip_lines)
        self.meta.setToolTip(tooltip)
        self.setToolTip(tooltip)

class MainWindow(QMainWindow):
    def __init__(self, *, live: bool = True) -> None:
        super().__init__()
        self._live = live
        self._started_monotonic = time.monotonic()
        self._quit_requested = False
        self._quit_reason: str | None = None
        self._active_threads: set[QThread] = set()
        self._tray_health: tuple[bool, bool] | None = None
        self._watchdog_ticks = 0
        self._refresh_timer: QTimer | None = None
        self.setMinimumSize(920, 620)
        self.resize(920, 620)
        log.info("event=main_window_init_start live=%s", live)
        self.config = load_config()
        self.runtime = QuotaDeckRuntime(self.config)
        self.worker: Worker | None = None
        self.preview_worker: PreviewWorker | None = None
        self.admin_worker: CursorAdminConnectWorker | None = None
        self.rows: list[AccountRow] = []
        self._busy = False
        self._display_refresh_pending = False
        self._playlist_infeasible = False
        self.tray_show = None
        self.tray_upload = None
        self.tray_logs = None
        self.tray_quit = None
        self.tray_menu = None
        self.tray = None
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        left_host = QWidget()
        left = QVBoxLayout(left_host)
        left.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size:16px; font-weight:600;")
        self.accounts_hint_btn = HintButton(ICON_INFO)
        self.lang_btn = QPushButton()
        self.lang_btn.setObjectName("lang")
        self.lang_btn.clicked.connect(self.toggle_language)
        header.addWidget(self.title, 1)
        header.addWidget(self.accounts_hint_btn)
        header.addWidget(self.lang_btn)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#8CB4B0;")
        self.hint.hide()
        left.addLayout(header)
        left.addWidget(self.hint)
        self.list = QListWidget()
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list.setMinimumHeight(160)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left.addWidget(self.list, 1)
        form = QFormLayout()
        form.setSpacing(6)
        # QPaintDevice.metric() is a virtual Qt method. A widget named `metric`
        # shadows it so layout/paint calls raise TypeError: QComboBox is not
        # callable, which the event loop reports as an unhandled exception.
        self.metric_combo = QComboBox()
        self.metric_combo.currentIndexChanged.connect(self._metric_changed)
        self.metric_hint = HintButton(ICON_INFO)
        self.period = QComboBox()
        self.period.currentIndexChanged.connect(self._cumulative_setting_changed)
        self.period_hint = HintButton(ICON_INFO)
        self.currency = QComboBox()
        self.currency.currentIndexChanged.connect(self._cumulative_setting_changed)
        self.currency_hint = HintButton(ICON_INFO)
        self.fx_auto = QCheckBox()
        self.fx_auto.setChecked(self.config.fx_auto)
        self.fx_auto.stateChanged.connect(self._cumulative_setting_changed)
        self.exchange_rate = Stepper(
            minimum=round(MIN_USD_TO_KRW_RATE),
            maximum=round(MAX_USD_TO_KRW_RATE),
            value=round(self.config.usd_to_krw_rate),
        )
        self.exchange_rate.valueChanged.connect(self._cumulative_setting_changed)
        self.fx_status = QLabel("")
        self.fx_status.setStyleSheet("color:#8CB4B0;")
        self.exchange_hint = HintButton(ICON_EXCHANGE)
        fx_row = QWidget()
        fx_layout = QHBoxLayout(fx_row)
        fx_layout.setContentsMargins(0, 0, 0, 0)
        fx_layout.setSpacing(6)
        fx_layout.addWidget(self.fx_auto)
        fx_layout.addWidget(self.exchange_rate, 1)
        fx_layout.addWidget(self.fx_status)
        self.mode = QComboBox()
        self.mode.currentIndexChanged.connect(lambda _index: self.update_metric_hint())
        self.order_hint = HintButton(ICON_BRAIN)
        self.poll = Stepper(minimum=15, maximum=600, value=self.config.poll_seconds)
        self.poll.setToolTip("")
        self.poll_hint = HintButton(ICON_INFO)
        self.hold = Stepper(minimum=2, maximum=20, value=self.config.scene_hold_seconds)
        self.hold.valueChanged.connect(self.update_hold_cycle)
        self.hold_cycle = QLabel("")
        self.hold_cycle.setStyleSheet("color:#8CB4B0;")
        self.hold_hint = HintButton(ICON_CLOCK)
        hold_row = QWidget()
        hold_layout = QHBoxLayout(hold_row)
        hold_layout.setContentsMargins(0, 0, 0, 0)
        hold_layout.setSpacing(6)
        hold_layout.addWidget(self.hold, 1)
        hold_layout.addWidget(self.hold_cycle)
        self.min_up = Stepper(minimum=1, maximum=120, value=self.config.min_upload_minutes)
        self.min_up.valueChanged.connect(self.update_flash_label)
        self.upload_hint = HintButton(ICON_CLOCK)
        self.startup = QCheckBox()
        self.startup.setChecked(self.config.launch_at_startup)
        self.lbl_metric = QLabel()
        self.lbl_period = QLabel()
        self.lbl_currency = QLabel()
        self.lbl_exchange_rate = QLabel()
        self.lbl_mode = QLabel()
        self.lbl_poll = QLabel()
        self.lbl_hold = QLabel()
        self.lbl_upload = QLabel()
        self.metric_row = _wrap_with_hint(self.metric_combo, self.metric_hint)
        self.period_row = _wrap_with_hint(self.period, self.period_hint)
        self.currency_row = _wrap_with_hint(self.currency, self.currency_hint)
        self.fx_row = _wrap_with_hint(fx_row, self.exchange_hint)
        self.mode_row = _wrap_with_hint(self.mode, self.order_hint)
        self.poll_row = _wrap_with_hint(self.poll, self.poll_hint)
        self.hold_row = _wrap_with_hint(hold_row, self.hold_hint)
        self.upload_row = _wrap_with_hint(self.min_up, self.upload_hint)
        form.addRow(self.lbl_metric, self.metric_row)
        form.addRow(self.lbl_period, self.period_row)
        form.addRow(self.lbl_currency, self.currency_row)
        form.addRow(self.lbl_exchange_rate, self.fx_row)
        form.addRow(self.lbl_mode, self.mode_row)
        form.addRow(self.lbl_poll, self.poll_row)
        form.addRow(self.lbl_hold, self.hold_row)
        form.addRow(self.lbl_upload, self.upload_row)
        form.addRow(self.startup)
        self.timing_hint = QLabel()
        self.timing_hint.setWordWrap(True)
        self.timing_hint.setStyleSheet("color:#8CB4B0;")
        self.timing_hint.hide()
        self.price_hint = QLabel()
        self.price_hint.setWordWrap(True)
        self.price_hint.setStyleSheet("color:#8CB4B0;")
        self.price_hint.hide()
        self.krw_unit_hint = QLabel()
        self.krw_unit_hint.setWordWrap(True)
        self.krw_unit_hint.setStyleSheet("color:#F0C440; font-weight:600;")
        self.krw_unit_hint.hide()
        left.addLayout(form)
        left.addWidget(self.krw_unit_hint)
        left.addWidget(self.timing_hint)
        left.addWidget(self.price_hint)
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
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setWidget(left_host)
        left_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        preview_header = QHBoxLayout()
        self.preview_title = QLabel()
        self.keyboard = QLabel("")
        self.keyboard_hint = HintButton(ICON_KEYBOARD)
        preview_header.addWidget(self.preview_title, 1)
        preview_header.addWidget(self.keyboard)
        preview_header.addWidget(self.keyboard_hint)
        self.preview = LcdPreview(3)
        status_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.lock_hint = HintButton(ICON_LOCK)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.lock_hint)
        flash_row = QHBoxLayout()
        self.flash = QLabel("")
        self.flash.setStyleSheet("color:#8CB4B0;")
        self.flash_hint = HintButton(ICON_CLOCK)
        flash_row.addWidget(self.flash, 1)
        flash_row.addWidget(self.flash_hint)
        missing_row = QHBoxLayout()
        self.missing = QLabel("")
        self.missing.setStyleSheet("color:#8CB4B0;")
        self.missing_hint = HintButton(ICON_LOCK)
        missing_row.addWidget(self.missing, 1)
        missing_row.addWidget(self.missing_hint)
        right.addLayout(preview_header)
        right.addWidget(self.preview, 1)
        right.addLayout(status_row)
        right.addLayout(flash_row)
        right.addLayout(missing_row)
        layout.addWidget(left_scroll, 3)
        layout.addLayout(right, 2)
        self.setCentralWidget(root)
        self.reload_accounts()
        self.apply_language()
        if live:
            # Show the tray only after the window has been constructed. A
            # startup exception can no longer create an icon that immediately
            # vanishes without a usable window behind it.
            self.tray = attach_tray(self)
            self._apply_tray_language()
            self._tray_watchdog = QTimer(self)
            self._tray_watchdog.setObjectName("tray-watchdog")
            self._tray_watchdog.timeout.connect(self._check_tray_health)
            self._tray_watchdog.start(60_000)
            self._check_tray_health()
            # A single-shot timer is restarted only after the current worker
            # finishes. This makes the configured poll cadence real without
            # permitting overlapping provider/device operations.
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setObjectName("usage-refresh")
            self._refresh_timer.setSingleShot(True)
            self._refresh_timer.timeout.connect(self._automatic_refresh)
            self._schedule_refresh(0)
        else:
            self._tray_watchdog = None
        log.info("event=main_window_init_ready live=%s accounts=%d", live, len(self.config.accounts))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(920, 620)

    def _lang(self) -> str:
        return "en" if self.config.ui_language == "en" else "ko"

    def _t(self, key: str, **kwargs: object) -> str:
        return tr(self._lang(), key, **kwargs)
    def apply_language(self) -> None:
        current_metric = self.metric_combo.currentData() or self.config.metric_mode.value
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        self.metric_combo.addItem(self._t("metric_quota"), "quota")
        self.metric_combo.addItem(self._t("metric_cumulative"), "cumulative")
        metric_index = self.metric_combo.findData(current_metric)
        self.metric_combo.setCurrentIndex(metric_index if metric_index >= 0 else 0)
        self.metric_combo.blockSignals(False)

        current_period = self.period.currentData() or self.config.cumulative_period.value
        self.period.blockSignals(True)
        self.period.clear()
        self.period.addItem(self._t("period_daily"), UsagePeriod.DAILY.value)
        self.period.addItem(self._t("period_monthly"), UsagePeriod.MONTHLY.value)
        period_index = self.period.findData(current_period)
        self.period.setCurrentIndex(period_index if period_index >= 0 else 1)
        self.period.blockSignals(False)

        current_currency = self.currency.currentData() or self.config.cost_currency.value
        self.currency.blockSignals(True)
        self.currency.clear()
        self.currency.addItem(self._t("currency_krw"), CostCurrency.KRW.value)
        self.currency.addItem(self._t("currency_usd"), CostCurrency.USD.value)
        currency_index = self.currency.findData(current_currency)
        self.currency.setCurrentIndex(currency_index if currency_index >= 0 else 0)
        self.currency.blockSignals(False)

        current = self.mode.currentData() or self.config.display_mode.value
        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItem(glyph_icon(ICON_BRAIN), self._t("mode_smart"), "smart")
        self.mode.addItem(glyph_icon(ICON_PIN), self._t("mode_fixed"), "fixed")
        index = self.mode.findData(current)
        self.mode.setCurrentIndex(index if index >= 0 else 0)
        self.mode.blockSignals(False)
        self.setWindowTitle(self._t("window_title"))
        self.title.setText(self._t("accounts_title"))
        self.title.setToolTip(self._t("accounts_hint"))
        self.hint.setText(self._t("accounts_hint"))
        self.accounts_hint_btn.apply_copy(
            self._t("hint_info"),
            f"{self._t('accounts_hint')}\n{self._t('cursor_hint')}",
        )
        for row in self.rows:
            row.apply_language(
                self._lang(),
                source=self.config.active_cursor_source(row.account.account_id),
            )
        self.lbl_metric.setText(self._t("metric_mode"))
        self.lbl_period.setText(self._t("comparison_period"))
        self.lbl_currency.setText(self._t("cost_currency"))
        self.lbl_exchange_rate.setText(self._t("exchange_rate"))
        self.lbl_mode.setText(self._t("display_mode"))
        self.lbl_poll.setText(self._t("poll"))
        self.lbl_hold.setText(self._t("hold"))
        self.lbl_upload.setText(self._t("min_upload"))
        self.poll.setSuffix(self._t("suffix_sec"))
        self.hold.setSuffix(self._t("suffix_sec"))
        self.min_up.setSuffix(self._t("suffix_min"))
        self.exchange_rate.setSuffix(self._t("exchange_rate_suffix"))
        self.fx_auto.setText(self._t("fx_auto"))
        self.poll.setToolTip(self._t("poll_tip"))
        self.hold.setToolTip(self._t("hold_tip"))
        self.min_up.setToolTip(self._t("upload_tip"))
        self.period.setToolTip(self._t("period_tip"))
        self.currency.setToolTip(self._t("currency_tip"))
        self.exchange_rate.setToolTip(self._t("exchange_rate_tip"))
        self.fx_auto.setToolTip(self._t("fx_auto_tip"))
        self.metric_combo.setToolTip(self._t("metric_tip"))
        self.mode.setToolTip(self._t("mode_smart_tip") if (self.mode.currentData() or "smart") == "smart" else self._t("mode_fixed_tip"))
        self.metric_hint.apply_copy(self._t("hint_info"), self._t("metric_tip"))
        self.period_hint.apply_copy(self._t("hint_info"), self._t("period_tip"))
        self.currency_hint.apply_copy(self._t("hint_info"), self._t("currency_tip"))
        self.poll_hint.apply_copy(self._t("hint_info"), self._t("poll_tip"))
        self.upload_hint.apply_copy(self._t("hint_clock"), self._t("upload_tip"))
        self.krw_unit_hint.setText(self._t("krw_unit_hint"))
        self.timing_hint.setText(self._t("timing_hint"))
        self.update_metric_hint()
        self.update_hold_cycle()
        self.update_fx_status()
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
        self._apply_tray_language()
        self.update_flash_label()
        self.update_keyboard_status()
        self.update_missing_label()
        self.update_lock_status()

    def _metric_changed(self, _index: int) -> None:
        self.update_metric_hint()
        self._restore_cached_snapshots()
        self._refresh_display_async(save=False)

    def _cumulative_setting_changed(self, _value: int) -> None:
        self.update_metric_hint()
        self.update_fx_status()
        self._restore_cached_snapshots()
        self._refresh_display_async(save=False)

    def update_metric_hint(self) -> None:
        cumulative = (
            self.metric_combo.currentData() or self.config.metric_mode.value
        ) == "cumulative"
        currency = self.currency.currentData() or self.config.cost_currency.value
        uses_krw = currency == CostCurrency.KRW.value
        for widget in (self.lbl_period, self.period_row):
            widget.setVisible(cumulative)
        for widget in (self.lbl_currency, self.currency_row):
            widget.setVisible(cumulative)
        for widget in (self.lbl_exchange_rate, self.fx_row):
            widget.setVisible(cumulative and uses_krw)
        self.krw_unit_hint.setVisible(False)
        self.price_hint.setVisible(False)
        if cumulative:
            self.price_hint.setText(
                self._t(
                    "price_hint" if uses_krw else "price_hint_usd",
                    date=_price_catalog_date(),
                    rate=self.exchange_rate.value(),
                )
            )
            self.exchange_hint.apply_copy(
                self._t("hint_exchange"),
                "\n".join(
                    part
                    for part in (
                        self._t("fx_auto_tip"),
                        self._t("exchange_rate_tip"),
                        self._t("krw_unit_hint") if uses_krw else "",
                        self.price_hint.text(),
                    )
                    if part
                ),
            )
        smart = (self.mode.currentData() or "smart") == "smart"
        self.order_hint.setIcon(glyph_icon(ICON_BRAIN if smart else ICON_PIN))
        self.order_hint.apply_copy(
            self._t("hint_order"),
            self._t("mode_smart_tip") if smart else self._t("mode_fixed_tip"),
        )
        self.mode.setToolTip(self._t("mode_smart_tip") if smart else self._t("mode_fixed_tip"))

    def update_hold_cycle(self) -> None:
        enabled = self._lcd_enabled_count()
        hold = self.hold.value()
        cycle = cycle_seconds(enabled, hold)
        self.hold_cycle.setText(self._t("hold_cycle", hold=hold, cycle=cycle))
        tip = self._t("hold_cycle_tip", hold=hold, count=enabled, cycle=cycle)
        planned = self._planned_enabled_count()
        needed = required_playlist_frames(planned, hold * 1000)
        was_blocked = self._playlist_infeasible
        if needed > LCD_MAX_FRAMES:
            self._playlist_infeasible = True
            tip = "\n".join(
                (
                    tip,
                    self._t(
                        "playlist_budget_impossible",
                        count=planned,
                        hold=hold,
                        required=needed,
                        limit=LCD_MAX_FRAMES,
                    ),
                )
            )
        else:
            self._playlist_infeasible = False
            extra = [self._t("playlist_budget_hint")]
            if needed > LCD_DEFAULT_BUDGET:
                extra.append(
                    self._t(
                        "playlist_budget_payload",
                        required=needed,
                        megabytes=f"{payload_padded_size(needed) / 1_000_000:.1f}",
                    )
                )
            tip = "\n".join((tip, *extra))
            if was_blocked:
                self._schedule_refresh(0)
        self.hold_cycle.setToolTip(tip)
        self.hold.setToolTip(f"{self._t('hold_tip')}\n{tip}")
        self.hold_hint.apply_copy(self._t("hint_clock"), f"{self._t('hold_tip')}\n{tip}")

    def _planned_enabled_count(self) -> int:
        """Enabled cards including unconnected Cursor that may appear later."""

        checked = [row.to_config() for row in self.rows if row.check.isChecked()]
        return enabled_account_count(checked)

    def _lcd_enabled_count(self) -> int:
        checked = [row for row in self.rows if row.check.isChecked()]
        rows = checked or list(self.rows)
        metric = self.metric_combo.currentData() or self.config.metric_mode.value
        if metric != "cumulative":
            return len(rows)
        from quotadeck.usage.display import visible_on_cumulative_lcd

        visible = 0
        period = str(self.period.currentData() or self.config.cumulative_period.value)
        for row in rows:
            key = f"{row.account.provider}:{row.account.account_id}"
            snap = self.runtime.state.last_cumulative.get((key, period))
            if snap is not None and not visible_on_cumulative_lcd(snap):
                continue
            visible += 1
        return visible

    def update_fx_status(self) -> None:
        quote = getattr(self.runtime, "state", None)
        fx: FxRateQuote | None = getattr(quote, "fx_quote", None) if quote is not None else None
        automatic = self.fx_auto.isChecked()
        # In Auto mode the spin box is only a fallback, not the effective
        # quote. Hide it so the live/cache rate shown alongside the checkbox
        # cannot be mistaken for a fixed 1,400 KRW rate.
        self.exchange_rate.setVisible(not automatic)
        if not automatic:
            text = self._t("fx_source_manual", rate=self.exchange_rate.value())
        elif fx is None:
            text = self._t("fx_pending")
        elif fx.reason and not fx.available:
            text = self._t("fx_source_error", reason=mask_text(fx.reason))
        elif fx.fallback is FxFallback.LIVE:
            text = self._t(
                "fx_source_live",
                as_of=fx.as_of or "—",
                rate=fx.usd_to_krw,
            )
        elif fx.fallback is FxFallback.LAST_KNOWN_GOOD:
            text = self._t(
                "fx_source_cache",
                as_of=fx.as_of or "—",
                rate=fx.usd_to_krw,
            )
        else:
            text = self._t(
                "fx_source_manual",
                rate=fx.usd_to_krw if fx.available else self.exchange_rate.value(),
            )
        self.fx_status.setText(text)
        self.fx_status.setToolTip(text)
        extra = []
        if fx is not None:
            extra.append(text)
            if fx.source:
                extra.append(fx.source)
            if fx.reason:
                extra.append(mask_text(fx.reason))
        self.exchange_hint.apply_copy(
            self._t("hint_exchange"),
            "\n".join(
                part
                for part in (
                    self._t("fx_auto_tip"),
                    self._t("exchange_rate_tip"),
                    self._t("krw_unit_hint"),
                    *extra,
                )
                if part
            ),
        )

    def _restore_cached_snapshots(self) -> None:
        runtime = self.runtime
        if not hasattr(runtime, "cached_snapshots"):
            return
        cfg = self.collect_config()
        snapshots = runtime.cached_snapshots(
            keys=[f"{item.provider}:{item.account_id}" for item in cfg.accounts],
            metric=cfg.metric_mode,
            period=cfg.cumulative_period,
        )
        if snapshots:
            self._apply_snapshots(snapshots)
            return
        for row in self.rows:
            row.set_snapshot(None)

    def _adopt_runtime_config(self, candidate: AppConfig) -> None:
        self.config = candidate
        if candidate == getattr(self.runtime, "config", None):
            return
        previous = getattr(self.runtime, "state", None)
        self.runtime = QuotaDeckRuntime(candidate)
        if hasattr(self.runtime, "adopt_display_state"):
            self.runtime.adopt_display_state(previous)

    def _refresh_display_async(self, *, save: bool = False) -> None:
        if not self._live or self._quit_requested:
            return
        if self._busy:
            self._display_refresh_pending = True
            return
        if self._reject_infeasible_playlist(modal=save):
            return
        if save:
            if not self.apply():
                return
        else:
            self._adopt_runtime_config(self.collect_config())
        self.set_busy(True)
        self.status.setText(self._t("status_reading"))
        self.preview_worker = PreviewWorker(self.runtime, self)
        self.preview_worker.done.connect(self._on_preview)
        self.preview_worker.failed.connect(self._on_automatic_fail)
        try:
            self._start_thread(self.preview_worker, "display-refresh")
        except Exception as exc:
            self._on_automatic_fail(mask_text(str(exc)))

    def _apply_tray_language(self) -> None:
        if self.tray_show is None:
            return
        self.tray_show.setText(self._t("tray_open"))
        self.tray_upload.setText(self._t("tray_upload"))
        self.tray_logs.setText(self._t("tray_logs"))
        self.tray_quit.setText(self._t("tray_quit"))
    def toggle_language(self) -> None:
        cfg = self.collect_config()
        cfg.ui_language = "en" if self._lang() == "ko" else "ko"
        self.config = cfg
        try:
            save_config(cfg)
        except SceneBudgetError:
            stored = load_config()
            stored.ui_language = cfg.ui_language
            save_config(stored, validate_playlist=False)
        self.apply_language()
    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        enabled = not busy
        for button in (self.detect_btn, self.preview_btn, self.apply_btn, self.upload_btn):
            button.setEnabled(enabled)
        for row in self.rows:
            row.set_actions_enabled(enabled)
        if self.tray_upload is not None:
            self.tray_upload.setEnabled(enabled)
        if self.tray_quit is not None:
            self.tray_quit.setEnabled(enabled)
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
            row.check.stateChanged.connect(lambda *_args: self.update_hold_cycle())
            self._bind_cursor_row(row)
            self.rows.append(row)
        self.update_missing_label()
        self.update_hold_cycle()
        self._restore_cached_snapshots()

    def _bind_cursor_row(self, row: AccountRow) -> None:
        account_id = row.account.account_id
        row.apply_language(
            self._lang(),
            source=self.config.active_cursor_source(account_id),
        )
        if row.account.provider != "cursor":
            return
        row.connect_csv.connect(lambda: self._import_cursor_csv(account_id))
        row.update_csv.connect(lambda: self._import_cursor_csv(account_id))
        row.disconnect_usage.connect(lambda: self._disconnect_cursor_usage(account_id))
        row.connect_admin.connect(lambda: self._connect_cursor_admin(account_id))
        row.update_admin.connect(lambda: self._refresh_cursor_runtime(force_admin=False))

    def _cursor_accounts(self) -> list[AccountConfig]:
        return [row.to_config() for row in self.rows if row.account.provider == "cursor"]

    def _choose_cursor_account(self, preferred: str | None = None) -> str | None:
        accounts = self._cursor_accounts()
        if not accounts:
            QMessageBox.information(self, "QuotaDeck", self._t("cursor_no_account"))
            return None
        if preferred:
            return preferred
        if len(accounts) == 1:
            return accounts[0].account_id
        labels = [f"{item.alias} ({item.account_id})" for item in accounts]
        chosen, ok = QInputDialog.getItem(
            self,
            "QuotaDeck",
            self._t("cursor_pick_account"),
            labels,
            0,
            False,
        )
        if not ok:
            return None
        index = labels.index(chosen)
        return accounts[index].account_id

    def _refresh_cursor_runtime(self, *, force_admin: bool = False) -> None:
        candidate = self.collect_config()
        try:
            save_config(candidate)
        except SceneBudgetError as exc:
            log.warning("event=cursor_runtime_save_rejected_playlist error=%s", exc)
            self._playlist_infeasible = True
            self._show_failure(playlist_budget_message(self._lang(), exc))
        self._adopt_runtime_config(candidate)
        if hasattr(self.runtime.cumulative, "invalidate"):
            self.runtime.cumulative.invalidate(force_admin=force_admin)
        self.runtime.state.last_cumulative.clear()
        for row in self.rows:
            row.apply_language(
                self._lang(),
                source=self.config.active_cursor_source(row.account.account_id),
            )
        self.update_hold_cycle()
        self._refresh_display_async(save=False)

    def _confirm_cursor_csv_guide(self) -> bool:
        return show_cursor_guide_dialog(
            self,
            self._lang(),
            title_key="cursor_csv_guide_title",
            body_key="cursor_csv_guide_body",
            links=(("cursor_csv_guide_open", CURSOR_ANALYTICS_URL),),
            continue_key="cursor_csv_guide_continue",
            cancel_key="cursor_csv_guide_cancel",
        )

    def _confirm_cursor_admin_guide(self) -> bool:
        return show_cursor_guide_dialog(
            self,
            self._lang(),
            title_key="cursor_admin_guide_title",
            body_key="cursor_admin_guide_body",
            links=(
                ("cursor_admin_guide_open_api", CURSOR_API_DOCS_URL),
                ("cursor_admin_guide_open_admin", CURSOR_ADMIN_API_DOCS_URL),
            ),
            continue_key="cursor_admin_guide_continue",
            cancel_key="cursor_admin_guide_cancel",
        )

    def _import_cursor_csv(self, account_id: str | None = None) -> None:
        if self._busy:
            return
        if not self._confirm_cursor_csv_guide():
            return
        selected = self._choose_cursor_account(account_id)
        if not selected:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            self._t("cursor_import_title"),
            "",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            from quotadeck.usage.cursor_import import (
                bind_imported_cursor_csv,
                import_cursor_csv,
                release_unused_cursor_admin_key,
            )

            stored = import_cursor_csv(path, selected)
            self.config = bind_imported_cursor_csv(self.collect_config(), selected, stored)
            release_unused_cursor_admin_key(self.config)
        except Exception as exc:
            log.exception("event=cursor_csv_import_failed")
            QMessageBox.warning(
                self,
                "QuotaDeck",
                self._t("cursor_import_bad", reason=mask_text(str(exc))),
            )
            return
        self.status.setText(self._t("cursor_import_ok"))
        self._refresh_cursor_runtime()

    def _disconnect_cursor_usage(self, account_id: str) -> None:
        if self._busy:
            return
        try:
            from quotadeck.usage.cursor_import import disconnect_cursor_account

            self.config = disconnect_cursor_account(self.collect_config(), account_id)
        except Exception as exc:
            log.exception("event=cursor_disconnect_failed")
            self._show_failure(mask_text(str(exc)))
            return
        self.status.setText(self._t("cursor_disconnected"))
        self._refresh_cursor_runtime()

    def _connect_cursor_admin(self, account_id: str | None = None) -> None:
        if self._busy:
            return
        if not self._confirm_cursor_admin_guide():
            return
        selected = self._choose_cursor_account(account_id)
        if not selected:
            return
        email = ""
        try:
            from quotadeck.core.models import AccountRef
            from quotadeck.providers.cursor.statedb import load_cursor_auth_for

            row = next(
                (item for item in self.rows if item.account.account_id == selected),
                None,
            )
            account = row.to_config() if row is not None else None
            auth = load_cursor_auth_for(
                AccountRef(
                    provider="cursor",
                    account_id=selected,
                    display_name=selected,
                    source_path=account.source_path if account else "",
                )
            )
            email = (auth.email if auth is not None else "") or ""
        except Exception:
            email = ""
        if not email:
            QMessageBox.warning(self, "QuotaDeck", self._t("cursor_admin_missing_user"))
            return
        key, ok = QInputDialog.getText(
            self,
            self._t("cursor_admin_title"),
            self._t("cursor_admin_prompt"),
            QLineEdit.EchoMode.Password,
        )
        if not ok or not str(key).strip():
            return
        self.set_busy(True)
        self.status.setText(self._t("cursor_admin_testing"))
        self.admin_worker = CursorAdminConnectWorker(
            selected,
            email,
            str(key).strip(),
            self,
        )
        self.admin_worker.succeeded.connect(self._on_cursor_admin_validated)
        self.admin_worker.failed.connect(self._on_cursor_admin_validation_failed)
        try:
            self._start_thread(self.admin_worker, "cursor-admin-connect")
        except Exception as exc:
            self._on_cursor_admin_validation_failed(mask_text(str(exc)))
        finally:
            key = ""

    def _on_cursor_admin_validated(self, account_id: str, email: str) -> None:
        existing = self.config.cursor_binding(account_id)
        self.config = upsert_cursor_binding(
            self.collect_config(),
            CursorUsageBinding(
                account_id=account_id,
                source="admin_api",
                csv_path=existing.csv_path if existing else "",
                imported_at=existing.imported_at if existing else "",
                admin_email=email,
                admin_user_id=account_id,
            ),
        )
        self.status.setText(self._t("cursor_admin_ok"))
        self._refresh_cursor_runtime(force_admin=False)

    def _on_cursor_admin_validation_failed(self, reason: str) -> None:
        QMessageBox.warning(
            self,
            "QuotaDeck",
            self._t("cursor_admin_bad", reason=reason),
        )
        self.status.setText(self._t("cursor_admin_bad", reason=reason))

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
        mode = self.mode.currentData() or self.mode.currentText() or self.config.display_mode.value
        metric = (
            self.metric_combo.currentData()
            or self.metric_combo.currentText()
            or self.config.metric_mode.value
        )
        period = (
            self.period.currentData()
            or self.period.currentText()
            or self.config.cumulative_period.value
        )
        currency = (
            self.currency.currentData()
            or self.currency.currentText()
            or self.config.cost_currency.value
        )
        return AppConfig(
            accounts=accounts,
            display_mode=DisplayMode(str(mode)),
            metric_mode=MetricMode(str(metric)),
            cumulative_period=UsagePeriod(str(period)),
            cost_currency=CostCurrency(str(currency)),
            usd_to_krw_rate=float(self.exchange_rate.value()),
            fx_auto=self.fx_auto.isChecked(),
            theme=self.config.theme,
            poll_seconds=self.poll.value(),
            scene_hold_seconds=self.hold.value(),
            min_upload_minutes=self.min_up.value(),
            max_age_minutes=self.config.max_age_minutes,
            daily_flash_limit=self.config.daily_flash_limit,
            frame_budget=self.config.frame_budget,
            launch_at_startup=self.startup.isChecked(),
            ui_language=self._lang(),
            cursor_bindings=list(self.config.cursor_bindings),
        )
    def detect(self) -> None:
        if self._busy:
            return
        log.info("event=account_detection_requested")
        try:
            found = discover_accounts()
        except Exception as exc:
            log.exception("event=account_detection_failed")
            self._show_failure(mask_text(str(exc)))
            return
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
        wanted = {(item.provider, item.account_id) for item in merged}
        self.config.cursor_bindings = [
            item
            for item in self.config.cursor_bindings
            if ("cursor", item.account_id) in wanted
        ]
        self.reload_accounts()
        self._restore_cached_snapshots()
        self.status.setText(self._t("status_found", count=len(found)))
        self._refresh_display_async(save=False)

    def apply(self) -> bool:
        try:
            candidate = self.collect_config()
            previous_budget = candidate.frame_budget
            save_config(candidate)
        except SceneBudgetError as exc:
            log.warning("event=config_save_rejected_playlist error=%s", exc)
            self._playlist_infeasible = True
            self._show_failure(playlist_budget_message(self._lang(), exc))
            return False
        except Exception as exc:
            log.exception("event=config_save_failed")
            self._show_failure(mask_text(str(exc)))
            return False
        raised_budget = candidate.frame_budget > previous_budget
        runtime_changed = candidate != self.runtime.config
        if runtime_changed and self._refresh_timer is not None:
            self._refresh_timer.stop()
        try:
            if runtime_changed:
                previous = self.runtime.state
                replacement = QuotaDeckRuntime(candidate)
                replacement.adopt_display_state(previous)
            else:
                replacement = self.runtime
        except Exception as exc:
            log.exception("event=runtime_reconfigure_failed")
            self._show_failure(mask_text(str(exc)))
            self._schedule_refresh()
            return False
        self.config = candidate
        self.runtime = replacement
        startup_ok = True
        try:
            set_launch_at_startup(self.config.launch_at_startup)
        except Exception:
            startup_ok = False
            log.exception(
                "event=startup_registration_failed enabled=%s",
                self.config.launch_at_startup,
            )
        self.update_flash_label()
        saved = self._t("status_saved") if startup_ok else self._t("status_saved_startup_failed")
        if raised_budget:
            saved = (
                f"{saved} · "
                + self._t(
                    "playlist_budget_raised",
                    previous=previous_budget,
                    budget=candidate.frame_budget,
                )
            )
        self.status.setText(saved)
        log.info(
            "event=config_saved accounts=%d hold_seconds=%d startup=%s startup_ok=%s",
            len(self.config.accounts),
            self.config.scene_hold_seconds,
            self.config.launch_at_startup,
            startup_ok,
        )
        self._schedule_refresh()
        return True

    def _schedule_refresh(self, delay_ms: int | None = None) -> None:
        if (
            not self._live
            or self._quit_requested
            or self._refresh_timer is None
        ):
            return
        interval = (
            max(15, int(self.config.poll_seconds)) * 1000
            if delay_ms is None
            else max(0, int(delay_ms))
        )
        self._refresh_timer.start(interval)

    def _reject_infeasible_playlist(self, *, modal: bool) -> bool:
        count = self._planned_enabled_count()
        hold = self.hold.value()
        needed = required_playlist_frames(count, hold * 1000)
        if needed <= LCD_MAX_FRAMES:
            self._playlist_infeasible = False
            return False
        self._playlist_infeasible = True
        message = self._t(
            "playlist_budget_impossible",
            count=count,
            hold=hold,
            required=needed,
            limit=LCD_MAX_FRAMES,
        )
        if modal:
            self._show_failure(message)
        else:
            self.status.setText(message)
        return True

    def _automatic_refresh(self) -> None:
        """Poll once and safely attempt a gated upload when hardware is ready."""

        if self._quit_requested:
            return
        if self._busy or self._active_threads:
            self._schedule_refresh()
            return
        if self._reject_infeasible_playlist(modal=False):
            return

        self.set_busy(True)
        self.status.setText(self._t("status_reading"))
        ready = False
        try:
            interfaces = enumerate_interfaces()
            ready = wired_mode_ok(interfaces) and not aula_software_running()
        except Exception:
            # Hardware absence must not stop provider monitoring or produce a
            # modal warning on every scheduled poll.
            log.warning("event=automatic_keyboard_probe_failed", exc_info=True)

        if ready:
            self.status.setText(self._t("status_uploading"))
            self.worker = Worker(self.runtime, force=False, parent=self)
            self.worker.done.connect(self._on_done)
            self.worker.failed.connect(self._on_automatic_fail)
            worker: QThread = self.worker
            kind = "automatic-upload"
        else:
            self.preview_worker = PreviewWorker(self.runtime, self)
            self.preview_worker.done.connect(self._on_preview)
            self.preview_worker.failed.connect(self._on_automatic_fail)
            worker = self.preview_worker
            kind = "automatic-preview"
        try:
            self._start_thread(worker, kind)
        except Exception as exc:
            self._on_automatic_fail(mask_text(str(exc)))
            self._schedule_refresh()

    def refresh_preview(self) -> None:
        if self._busy:
            return
        if not self.apply():
            return
        self.set_busy(True)
        self.status.setText(self._t("status_reading"))
        self.preview_worker = PreviewWorker(self.runtime, self)
        self.preview_worker.done.connect(self._on_preview)
        self.preview_worker.failed.connect(self._on_fail)
        try:
            self._start_thread(self.preview_worker, "preview")
        except Exception as exc:
            self._show_failure(mask_text(str(exc)))

    def upload_now(self) -> None:
        if self._busy:
            return
        if not self.apply():
            return
        log.info("event=keyboard_upload_requested")
        try:
            self.update_keyboard_status()
            interfaces = enumerate_interfaces()
        except Exception as exc:
            log.exception("event=keyboard_detection_failed")
            self._show_failure(mask_text(str(exc)))
            return
        if not wired_mode_ok(interfaces):
            log.warning("event=keyboard_upload_blocked reason=not_wired")
            QMessageBox.warning(self, "QuotaDeck", self._t("warn_usb"))
            return
        running = aula_software_running()
        if running:
            log.warning("event=keyboard_upload_blocked reason=aula_running count=%d", len(running))
            QMessageBox.warning(self, "QuotaDeck", self._t("warn_aula", names=", ".join(running)))
            return
        self.set_busy(True)
        self.status.setText(self._t("status_uploading"))
        self.worker = Worker(self.runtime, force=True, parent=self)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        try:
            self._start_thread(self.worker, "upload")
        except Exception as exc:
            self._show_failure(mask_text(str(exc)))

    def _start_thread(self, worker: QThread, kind: str) -> None:
        self._active_threads.add(worker)
        worker.setProperty("quotadeck_kind", kind)
        # A bound QObject slot supplies GUI-thread context. A context-less
        # lambda could execute in the worker thread and mutate widgets there.
        worker.finished.connect(self._thread_finished)
        worker.finished.connect(worker.deleteLater)
        log.info("event=worker_start kind=%s active=%d", kind, len(self._active_threads))
        try:
            worker.start()
        except Exception:
            self._active_threads.discard(worker)
            if self.worker is worker:
                self.worker = None
            if self.preview_worker is worker:
                self.preview_worker = None
            if self.admin_worker is worker:
                self.admin_worker = None
            worker.deleteLater()
            self.set_busy(False)
            log.exception("event=worker_start_failed kind=%s", kind)
            raise

    @Slot()
    def _thread_finished(self) -> None:
        worker = self.sender()
        if not isinstance(worker, QThread):
            log.error("event=worker_finished_unknown_sender")
            return
        kind = str(worker.property("quotadeck_kind") or worker.objectName() or "unknown")
        self._finish_thread(worker, kind)

    def _finish_thread(self, worker: QThread, kind: str) -> None:
        self._active_threads.discard(worker)
        if self.worker is worker:
            self.worker = None
        if self.preview_worker is worker:
            self.preview_worker = None
        if self.admin_worker is worker:
            self.admin_worker = None
        log.info("event=worker_finished kind=%s active=%d", kind, len(self._active_threads))
        if not self._active_threads:
            self.set_busy(False)
            if self._quit_requested:
                QTimer.singleShot(0, self._quit_now)
            elif self._display_refresh_pending:
                QTimer.singleShot(0, self._consume_pending_display_refresh)
            elif not self._playlist_infeasible:
                self._schedule_refresh()

    @Slot()
    def _consume_pending_display_refresh(self) -> None:
        if not self._display_refresh_pending:
            return
        if not self._live or self._quit_requested:
            self._display_refresh_pending = False
            return
        if self._busy or self._active_threads:
            return
        self._display_refresh_pending = False
        self._refresh_display_async(save=False)

    def _on_preview(self, snapshots, frames) -> None:
        self._apply_snapshots(snapshots)
        self._update_tray_status(snapshots)
        self.update_fx_status()
        self.update_lock_status(snapshots)
        if frames:
            self.preview.show_frames(frames)
        count = len(snapshots)
        enabled = self._lcd_enabled_count()
        hold = self.config.scene_hold_seconds
        self.status.setText(
            self._t(
                "status_preview",
                enabled=enabled,
                count=count,
                hold=hold,
                cycle=cycle_seconds(enabled, hold),
            )
        )
    def _on_done(self, message: str) -> None:
        self.status.setText(message)
        frames = self.runtime.state.last_frames
        if frames:
            self.preview.show_frames(frames)
        self._apply_snapshots(self.runtime.state.last_polled)
        self._update_tray_status(self.runtime.state.last_polled)
        self.update_fx_status()
        self.update_lock_status(self.runtime.state.last_polled)

    def _update_tray_status(
        self,
        snapshots: list[UsageSnapshot | CumulativeSnapshot],
    ) -> None:
        active_keys = {item.key for item in snapshots}
        worst = worst_severity(
            severity
            for key, severity in self.runtime.state.severity.items()
            if key in active_keys
        )
        if self.tray is not None:
            self.tray.setIcon(severity_icon(worst))
            uploaded = self.runtime.budget.last_upload
            if uploaded:
                self.tray.setToolTip(self._t("tray_last", time=uploaded.astimezone().strftime("%H:%M")))
    def _apply_snapshots(
        self,
        snapshots: list[UsageSnapshot | CumulativeSnapshot],
    ) -> None:
        if hasattr(self.runtime, "remember_snapshots"):
            self.runtime.remember_snapshots(list(snapshots))
        by_key = {item.key: item for item in snapshots}
        rate = (
            self.runtime.effective_usd_to_krw_rate()
            if hasattr(self.runtime, "effective_usd_to_krw_rate")
            else self.config.usd_to_krw_rate
        )
        for row in self.rows:
            row.set_snapshot(
                by_key.get(f"{row.account.provider}:{row.account.account_id}"),
                currency=self.config.cost_currency,
                usd_to_krw_rate=rate,
                krw_cost_label=self._t("currency_krw"),
            )
        self.update_hold_cycle()

    def _on_fail(self, message: str) -> None:
        log.warning("event=worker_failure_presented message=%r", message)
        if self._quit_requested:
            self.status.setText(message)
            return
        self._show_failure(message)

    def _on_automatic_fail(self, message: str) -> None:
        """Keep scheduled failures visible but non-modal while monitoring."""

        log.warning("event=automatic_refresh_failed message=%r", message)
        lowered = message.casefold()
        if "141" in message and ("frame" in lowered or "프레임" in message):
            self._playlist_infeasible = True
        self.status.setText(message)
        if self.runtime.state.last_polled:
            self._apply_snapshots(self.runtime.state.last_polled)
            self._update_tray_status(self.runtime.state.last_polled)
        elif self.tray is not None:
            self.tray.setIcon(severity_icon(Severity.ERROR))

    def _show_failure(self, message: str) -> None:
        QMessageBox.warning(self, "QuotaDeck", message)
        self.status.setText(message)
    def update_lock_status(self, snapshots: list | None = None) -> None:
        items = snapshots if snapshots is not None else getattr(self.runtime.state, "last_polled", [])
        reasons: list[str] = []
        for snap in items:
            error = getattr(snap, "error", None)
            status = getattr(snap, "status", "")
            provider = getattr(snap, "provider", "")
            if not error:
                continue
            masked = mask_text(str(error))
            if status == "offline":
                reasons.append(
                    self._t("lock_not_signed_in", reason=f"{provider}: {masked}")
                )
            elif status == "stale":
                reasons.append(self._t("lock_stale", reason=f"{provider}: {masked}"))
            else:
                reasons.append(
                    self._t("lock_signed_in_usage", reason=f"{provider}: {masked}")
                )
        if reasons:
            detail = "\n".join(reasons)
        else:
            detail = self._t("lock_ok")
        self.lock_hint.apply_copy(self._t("hint_lock"), detail)
        if reasons:
            self.status.setToolTip(detail)
        elif not self.status.toolTip():
            self.status.setToolTip(detail)

    def update_flash_label(self) -> None:
        budget = FlashBudget(
            min_interval=timedelta(minutes=self.min_up.value()),
            daily_limit=self.config.daily_flash_limit,
        )
        daily = budget.estimated_uncapped_daily_writes()
        years = budget.estimated_years(cap=False)
        self.flash.setText(
            self._t(
                "flash_compact",
                daily=daily,
                limit=budget.daily_limit,
            )
        )
        tip = self._t(
            "flash_tip",
            hours=ACTIVE_HOURS_PER_DAY,
            daily=daily,
            limit=budget.daily_limit,
            years=years,
        )
        self.flash.setToolTip(tip)
        self.flash_hint.apply_copy(self._t("hint_clock"), tip)
    def update_keyboard_status(self) -> None:
        interfaces = enumerate_interfaces()
        running = aula_software_running()
        if wired_mode_ok(interfaces):
            extra = self._t("kb_aula", names=", ".join(running)) if running else ""
            compact = self._t("kb_compact_wired")
            detail = self._t("kb_wired", extra=extra)
            self.keyboard.setStyleSheet("color:#3DDC97;")
        elif interfaces:
            compact = self._t("kb_compact_seen")
            detail = self._t("kb_seen")
            self.keyboard.setStyleSheet("color:#F0C440;")
        else:
            compact = self._t("kb_compact_missing")
            detail = self._t("kb_missing")
            self.keyboard.setStyleSheet("color:#FF783C;")
        self.keyboard.setText(compact)
        self.keyboard.setToolTip(detail)
        self.keyboard_hint.apply_copy(self._t("hint_keyboard"), detail)
    def update_missing_label(self) -> None:
        present = {item.provider for item in self.config.accounts}
        missing = [name for name in ("codex", "cursor", "claude", "grok") if name not in present]
        if not missing:
            detail = self._t("found_all")
            self.missing.setText(self._t("found_all_compact"))
            self.missing.setToolTip(detail)
            self.missing_hint.apply_copy(self._t("hint_lock"), detail)
            return
        hints = {
            "codex": self._t("hint_codex"),
            "cursor": self._t("hint_cursor"),
            "claude": self._t("hint_claude"),
            "grok": self._t("hint_grok"),
        }
        names = ", ".join(name.upper() for name in missing)
        detail = self._t(
            "missing",
            names=names,
            hints=" · ".join(hints[name] for name in missing),
        )
        self.missing.setText(self._t("missing_compact", names=names))
        self.missing.setToolTip(detail)
        self.missing_hint.apply_copy(self._t("hint_lock"), detail)
    def show_from_tray(self) -> None:
        log.info("event=tray_open_requested")
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_log_folder(self) -> None:
        directory = diagnostic_log_directory()
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        log.info("event=log_folder_open_requested success=%s path=%r", opened, directory)
        if not opened:
            self.status.setText(self._t("status_log_open_failed", path=directory))

    def _check_tray_health(self) -> None:
        if not self._live:
            return
        available = QSystemTrayIcon.isSystemTrayAvailable()
        visible = bool(self.tray is not None and self.tray.isVisible())
        geometry_valid = bool(self.tray is not None and self.tray.geometry().isValid())
        health = (available, visible)
        self._watchdog_ticks += 1
        changed = health != self._tray_health
        self._tray_health = health
        if changed or self._watchdog_ticks % 5 == 0:
            log.log(
                logging.INFO if available and visible else logging.WARNING,
                "event=tray_heartbeat uptime_s=%d available=%s visible=%s "
                "geometry_valid=%s window_visible=%s busy=%s active_workers=%d",
                int(time.monotonic() - self._started_monotonic),
                available,
                visible,
                geometry_valid,
                self.isVisible(),
                self._busy,
                len(self._active_threads),
            )
        if available and self.tray is not None and not visible:
            log.warning("event=tray_recovery_attempt")
            self.tray.show()

    def close_app(self) -> None:
        if self._quit_requested:
            return
        self._quit_requested = True
        self._quit_reason = "tray_quit"
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        running = [thread.objectName() or type(thread).__name__ for thread in self._active_threads if thread.isRunning()]
        log.info(
            "event=shutdown_requested reason=tray_quit tracked_workers=%d running_workers=%r",
            len(self._active_threads),
            running,
        )
        if self._active_threads:
            self.status.setText(self._t("status_quit_wait"))
            return
        self._quit_now()

    def _quit_now(self) -> None:
        log.info("event=shutdown_proceed reason=%s", self._quit_reason or "unknown")
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        if self._tray_watchdog is not None:
            self._tray_watchdog.stop()
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._live or self._quit_requested:
            log.info("event=window_close_accepted live=%s quit_requested=%s", self._live, self._quit_requested)
            event.accept()
            return
        available = QSystemTrayIcon.isSystemTrayAvailable()
        visible = bool(self.tray is not None and self.tray.isVisible())
        event.ignore()
        if available and visible:
            self.hide()
            log.info("event=window_hidden_to_tray active_workers=%d", len(self._active_threads))
            return
        log.warning(
            "event=window_hide_blocked tray_available=%s tray_visible=%s",
            available,
            visible,
        )
        self.show()


def run_app(session: DiagnosticSession | None = None) -> int:
    session = session or configure_diagnostics(
        "ui",
        console=sys.stderr is not None and not bool(getattr(sys, "frozen", False)),
    )
    session.install_qt_message_handler()
    session.event("qt_startup", qt=qVersion())
    app = QApplication(sys.argv)
    app.setApplicationName("QuotaDeck")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(APP_QSS)
    try:
        window = MainWindow()
    except Exception:
        session.exception("ui_startup_failed")
        raise
    app.aboutToQuit.connect(
        lambda: session.event(
            "about_to_quit",
            active_workers=len(window._active_threads),
            reason=window._quit_reason or "qt_or_os_quit",
            tray_visible=bool(window.tray is not None and window.tray.isVisible()),
            window_visible=window.isVisible(),
        )
    )
    window.show()
    session.event(
        "event_loop_started",
        tray_available=QSystemTrayIcon.isSystemTrayAvailable(),
        tray_visible=bool(window.tray is not None and window.tray.isVisible()),
    )
    exit_code = app.exec()
    session.event(
        "event_loop_returned",
        active_workers=len(window._active_threads),
        exit_code=exit_code,
        reason=window._quit_reason or "qt_or_os_quit",
    )
    effective_exit_code = exit_code
    if window._active_threads:
        effective_exit_code = exit_code or 1
        session.event(
            "event_loop_returned_with_active_workers",
            level=logging.CRITICAL,
            active_workers=len(window._active_threads),
        )
        session.mark_unclean_shutdown(
            "qt_exit_with_active_workers",
            exit_code=effective_exit_code,
        )
    elif window._quit_reason == "tray_quit":
        session.mark_shutdown("tray_quit", exit_code=exit_code)
    else:
        effective_exit_code = exit_code or 1
        session.mark_unclean_shutdown(
            "unexpected_event_loop_return",
            exit_code=effective_exit_code,
        )
    return effective_exit_code
