"""Monochrome GUI glyphs. Prefer a painted fallback over colour emoji."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyle

ICON_INFO = "info"
ICON_CLOCK = "clock"
ICON_EXCHANGE = "exchange"
ICON_BRAIN = "brain"
ICON_PIN = "pin"
ICON_KEYBOARD = "keyboard"
ICON_LOCK = "lock"

_STYLE_FALLBACKS = {
    ICON_INFO: QStyle.StandardPixmap.SP_MessageBoxInformation,
    ICON_LOCK: QStyle.StandardPixmap.SP_DialogNoButton,
}


def glyph_icon(kind: str, size: int = 16) -> QIcon:
    """Return a small monochrome icon, with a QStyle fallback when useful."""

    style = QApplication.instance().style() if QApplication.instance() else None
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    color = QColor("#C8D8DC")
    pen = QPen(color, max(1.0, size / 16.0))
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _paint(painter, kind, size)
    painter.end()
    icon = QIcon(pixmap)
    if style is not None and kind in _STYLE_FALLBACKS and pixmap.hasAlpha():
        # Keep the painted glyph as the primary; QStyle is used only when the
        # painted fallback could not be created.
        if pixmap.isNull():
            return style.standardIcon(_STYLE_FALLBACKS[kind])
    return icon


def _paint(painter: QPainter, kind: str, size: int) -> None:
    inset = max(1, size // 8)
    box = QRect(inset, inset, size - 2 * inset, size - 2 * inset)
    if kind == ICON_INFO:
        painter.drawEllipse(box)
        cx = box.center().x()
        painter.drawPoint(cx, box.top() + 2)
        painter.drawLine(cx, box.top() + 5, cx, box.bottom() - 1)
        return
    if kind == ICON_CLOCK:
        painter.drawEllipse(box)
        center = box.center()
        painter.drawLine(center, center + QPoint(0, -box.height() // 3))
        painter.drawLine(center, center + QPoint(box.width() // 4, box.height() // 8))
        return
    if kind == ICON_EXCHANGE:
        mid = box.center().y()
        painter.drawLine(box.left(), mid - 2, box.right() - 2, mid - 2)
        painter.drawLine(box.right() - 2, mid - 2, box.right() - 5, mid - 5)
        painter.drawLine(box.right(), mid + 2, box.left() + 2, mid + 2)
        painter.drawLine(box.left() + 2, mid + 2, box.left() + 5, mid + 5)
        return
    if kind == ICON_BRAIN:
        painter.drawEllipse(box.adjusted(0, 1, 0, -1))
        painter.drawLine(box.center().x(), box.top() + 2, box.center().x(), box.bottom() - 2)
        painter.drawArc(box.adjusted(2, 3, -2, -3), 30 * 16, 120 * 16)
        return
    if kind == ICON_PIN:
        painter.drawEllipse(QRect(box.left() + 2, box.top(), box.width() - 4, box.height() // 2))
        painter.drawLine(box.center().x(), box.top() + box.height() // 2, box.center().x(), box.bottom())
        return
    if kind == ICON_KEYBOARD:
        painter.drawRoundedRect(box, 2, 2)
        step = max(2, box.width() // 4)
        for index in range(3):
            x = box.left() + 2 + index * step
            painter.drawPoint(x, box.center().y() - 1)
        painter.drawLine(box.left() + 3, box.bottom() - 3, box.right() - 3, box.bottom() - 3)
        return
    if kind == ICON_LOCK:
        body = QRect(box.left() + 1, box.center().y() - 1, box.width() - 2, box.height() // 2 + 1)
        painter.drawRoundedRect(body, 1, 1)
        painter.drawArc(
            QRect(box.left() + 3, box.top(), box.width() - 6, box.height() // 2),
            0,
            180 * 16,
        )
        return
    painter.drawEllipse(box)
