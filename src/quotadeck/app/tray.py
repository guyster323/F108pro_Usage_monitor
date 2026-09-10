from __future__ import annotations

from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

from quotadeck.core.models import Severity

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

def attach_tray(window, runtime) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(severity_icon(None), window)
    tray.setToolTip("QuotaDeck")
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    show = QAction("QuotaDeck 열기", window)
    show.triggered.connect(window.show)
    upload = QAction("지금 키보드에 올리기", window)
    upload.triggered.connect(window.upload_now)
    quit_action = QAction("종료", window)
    quit_action.triggered.connect(window.close_app)
    menu.addAction(show)
    menu.addAction(upload)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: window.show() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    window.tray_show = show
    window.tray_upload = upload
    window.tray_quit = quit_action
    return tray
