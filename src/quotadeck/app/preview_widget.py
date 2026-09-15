from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy

from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH
from quotadeck.devices.aula_f108.payload import Frame

class LcdPreview(QLabel):
    def __init__(self, scale: int = 3) -> None:
        super().__init__()
        self._scale = scale
        self._frames: list[Frame] = []
        self._index = 0
        self._current = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setMinimumSize(LCD_WIDTH, LCD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#101418; border:1px solid #2a3238;")
        self.setText("No preview")

    def sizeHint(self) -> QSize:  # noqa: N802
        # Prefer 2x at the 920x620 design size; grow toward 3x when space allows.
        preferred = min(self._scale, 2)
        return QSize(LCD_WIDTH * preferred, LCD_HEIGHT * preferred)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return max(LCD_HEIGHT, int(width * LCD_HEIGHT / LCD_WIDTH))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._current is not None:
            self._paint(self._current)
    def show_image(self, image) -> None:
        self._timer.stop()
        self._frames = []
        self._paint(image)

    def show_frames(self, frames: list[Frame]) -> None:
        self._timer.stop()
        self._frames = list(frames)
        self._index = 0
        if not self._frames:
            self.setText("No preview")
            return
        self._paint(self._frames[0].image)
        if len(self._frames) > 1:
            self._timer.start(max(200, self._frames[0].delay_ms))
    def _advance(self) -> None:
        if not self._frames:
            self._timer.stop()
            return
        self._index = (self._index + 1) % len(self._frames)
        frame = self._frames[self._index]
        self._paint(frame.image)
        self._timer.start(max(200, frame.delay_ms))
    def _paint(self, image) -> None:
        self._current = image
        rgb = image.convert("RGB")
        data = rgb.tobytes()
        qimg = QImage(data, rgb.size[0], rgb.size[1], rgb.size[0] * 3, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg).scaled(
            self.width(),
            self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.setPixmap(pix)
