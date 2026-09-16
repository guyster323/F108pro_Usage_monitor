"""Compact, keyboard-accessible hover/help affordance."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QSizePolicy, QToolButton, QWidget

from quotadeck.app.icons import glyph_icon


class HintButton(QToolButton):
    """A small icon button whose detailed copy lives on hover and What's This."""

    def __init__(
        self,
        kind: str,
        *,
        name: str = "",
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setObjectName("hint")
        self.setIcon(glyph_icon(kind))
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(22, 22)
        self.setAutoRaise(True)
        self.apply_copy(name, description)
        self.clicked.connect(self.show_help)

    def apply_copy(self, name: str, description: str) -> None:
        self.setAccessibleName(name)
        self.setAccessibleDescription(description)
        self.setStatusTip(description)
        self.setToolTip(description)
        self.setWhatsThis(description)

    def show_help(self) -> None:
        title = self.accessibleName() or self._kind
        body = self.toolTip() or self.accessibleDescription()
        QMessageBox.information(self.window(), title, body)
