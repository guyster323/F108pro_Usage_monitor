from __future__ import annotations

import logging

from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from quotadeck.core.models import Severity

log = logging.getLogger("quotadeck.ui.tray")

def severity_icon(severity: Severity | None) -> QIcon:
    color = {
        Severity.HEALTHY: "#3DDC97",
        Severity.BUSY: "#50B4FF",
        Severity.CAUTION: "#F0C440",
        Severity.CRITICAL: "#FF783C",
        Severity.EXHAUSTED: "#FF4050",
        Severity.RESET: "#B4FF78",
        Severity.OFFLINE: "#5A6068",
        Severity.STALE: "#A08C50",
        Severity.ERROR: "#FF5050",
    }.get(severity or Severity.STALE, "#5A6068")
    pix = QPixmap(16, 16)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setBrush(QColor(color))
    painter.setPen(QColor("#101418"))
    painter.drawEllipse(1, 1, 14, 14)
    painter.end()
    return QIcon(pix)

def attach_tray(window) -> QSystemTrayIcon:
    available = QSystemTrayIcon.isSystemTrayAvailable()
    log.log(
        logging.INFO if available else logging.WARNING,
        "event=tray_attach_start system_available=%s",
        available,
    )
    tray = QSystemTrayIcon(severity_icon(None), window)
    tray.setObjectName("quotadeck-tray")
    tray.setToolTip("QuotaDeck")
    # QSystemTrayIcon does not own its context menu. Parent and retain it so a
    # Python garbage-collection cycle cannot invalidate the native menu.
    menu = QMenu(window)
    menu.setObjectName("quotadeck-tray-menu")
    show = QAction("QuotaDeck 열기", window)
    show.triggered.connect(window.show_from_tray)
    upload = QAction("지금 키보드에 올리기", window)
    upload.triggered.connect(window.upload_now)
    logs = QAction("진단 로그 폴더 열기", window)
    logs.triggered.connect(window.open_log_folder)
    quit_action = QAction("종료", window)
    quit_action.triggered.connect(window.close_app)
    menu.addAction(show)
    menu.addAction(upload)
    menu.addAction(logs)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.show_from_tray()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else log.info("event=tray_activated reason=%s", getattr(reason, "name", reason))
    )
    tray.destroyed.connect(
        lambda: log.info(
            "event=tray_destroyed quit_requested=%s",
            bool(getattr(window, "_quit_requested", False)),
        )
    )
    tray.show()
    window.tray_menu = menu
    window.tray_show = show
    window.tray_upload = upload
    window.tray_logs = logs
    window.tray_quit = quit_action
    log.info("event=tray_attach_complete visible=%s", tray.isVisible())
    return tray
