from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

from quotadeck.devices.aula_f108.constants import LCD_HEIGHT, LCD_WIDTH


class LcdPreview(QLabel):
    def __init__(self, scale: int = 3) -> None:
        super().__init__()
        self._scale = scale
        self.setFixedSize(LCD_WIDTH * scale, LCD_HEIGHT * scale)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#101418; border:1px solid #2a3238;")
        self.setText("No preview")

    def show_image(self, image) -> None:
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
