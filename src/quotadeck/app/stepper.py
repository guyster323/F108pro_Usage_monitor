from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class Stepper(QWidget):
    """Number control whose +/- hit targets match the drawn buttons."""

    valueChanged = Signal(int)
    def __init__(
        self,
        *,
        minimum: int,
        maximum: int,
        value: int,
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._minimum = minimum
        self._maximum = maximum
        self._value = value
        self._suffix = suffix
        self.display = QLabel()
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.display.setMinimumWidth(78)
        self.display.setStyleSheet(
            "background:#1A242C; color:#E8F0F4; border:1px solid #2A3238; padding:6px 8px;"
        )
        self.up = QPushButton("▲")
        self.down = QPushButton("▼")
        for button in (self.up, self.down):
            button.setFixedSize(28, 18)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(350)
            button.setAutoRepeatInterval(70)
            button.setStyleSheet(
                "QPushButton { background:#243038; color:#E8F0F4; border:1px solid #2A3238; padding:0; }"
                "QPushButton:hover { border-color:#3DDC97; color:#3DDC97; }"
                "QPushButton:pressed { background:#16332B; }"
                "QPushButton:disabled { color:#5A6870; }"
            )
        self.up.clicked.connect(self._inc)
        self.down.clicked.connect(self._dec)
        arrows = QVBoxLayout()
        arrows.setContentsMargins(0, 0, 0, 0)
        arrows.setSpacing(2)
        arrows.addWidget(self.up)
        arrows.addWidget(self.down)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.display, 1)
        row.addLayout(arrows)
        self._refresh()
    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802
        clamped = max(self._minimum, min(self._maximum, int(value)))
        changed = clamped != self._value
        self._value = clamped
        self._refresh()
        if changed:
            self.valueChanged.emit(self._value)
    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self._minimum = minimum
        self._maximum = maximum
        self.setValue(self._value)

    def setSuffix(self, suffix: str) -> None:  # noqa: N802
        self._suffix = suffix
        self._refresh()

    def _inc(self) -> None:
        self.setValue(self._value + 1)

    def _dec(self) -> None:
        self.setValue(self._value - 1)
    def _refresh(self) -> None:
        self.display.setText(f"{self._value}{self._suffix}")
        self.up.setEnabled(self._value < self._maximum)
        self.down.setEnabled(self._value > self._minimum)
