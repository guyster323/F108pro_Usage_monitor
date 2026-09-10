from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


class Stepper(QWidget):
    """Editable number control whose +/- hit targets match the drawn buttons."""

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
        if minimum > maximum:
            raise ValueError("minimum must not exceed maximum")
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        self._value = max(self._minimum, min(self._maximum, int(value)))
        self._suffix = suffix
        self.display = QLineEdit(str(self._value))
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.display.setMinimumWidth(78)
        self.display.setStyleSheet(
            "background:#1A242C; color:#E8F0F4; border:1px solid #2A3238; padding:6px 8px;"
        )
        self.display.setToolTip("Enter a value, then press Enter or move to another field.")
        self.display.editingFinished.connect(self.commit)
        self.suffix_label = QLabel()
        self.suffix_label.setStyleSheet("color:#8CB4B0;")
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
        row.addWidget(self.suffix_label)
        row.addLayout(arrows)
        self._refresh()

    def value(self) -> int:
        return self._value

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def commit(self) -> bool:
        """Apply the typed value without silently accepting an invalid value."""
        try:
            value = int(self.display.text().strip())
        except (TypeError, ValueError):
            return False
        if not self._minimum <= value <= self._maximum:
            return False
        changed = value != self._value
        self._value = value
        self._refresh()
        if changed:
            self.valueChanged.emit(self._value)
        return True

    def setValue(self, value: int) -> None:  # noqa: N802
        clamped = max(self._minimum, min(self._maximum, int(value)))
        changed = clamped != self._value
        self._value = clamped
        self._refresh()
        if changed:
            self.valueChanged.emit(self._value)
    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        if minimum > maximum:
            raise ValueError("minimum must not exceed maximum")
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
        self.display.setText(str(self._value))
        self.suffix_label.setText(self._suffix)
        self.up.setEnabled(self._value < self._maximum)
        self.down.setEnabled(self._value > self._minimum)
