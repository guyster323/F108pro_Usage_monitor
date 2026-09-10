from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH
from quotadeck.devices.aula_f108.payload import Frame

class LcdPreview(QLabel):
    def __init__(self, scale: int = 3) -> None:
        super().__init__()
        self._scale = scale
        self._frames: list[Frame] = []
        self._index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(LCD_WIDTH * scale, LCD_HEIGHT * scale)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#101418; border:1px solid #2a3238;")
        self.setText("No preview")
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
