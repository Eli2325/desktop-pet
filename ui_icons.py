"""共享小图标：控制台展开箭头、QComboBox 下拉箭头等。"""

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def combo_chevron_png_uri() -> str:
    """生成/更新配置目录下的 PNG，返回 file:// URI 供 QSS image: url() 使用（Fusion 对 SVG data URL 支持差）。"""
    from config_utils import get_config_dir

    cache = os.path.join(get_config_dir(), "ui_cache")
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, "combo_chevron.png")
    need = True
    if os.path.isfile(path):
        try:
            need = os.path.getsize(path) < 80
        except OSError:
            need = True
    if need:
        pm = chevron_pixmap(down=True, size=64, color="#334155")
        pm.save(path, "PNG")
    return Path(os.path.abspath(path)).as_uri()


def chevron_pixmap(*, down: bool = True, size: int = 14, color: str = "#334155") -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.75)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    cx = size / 2
    spread = size * 0.32
    if down:
        y1, y2 = size * 0.36, size * 0.64
        p.drawLine(int(cx - spread), int(y1), int(cx), int(y2))
        p.drawLine(int(cx), int(y2), int(cx + spread), int(y1))
    else:
        y1, y2 = size * 0.64, size * 0.36
        p.drawLine(int(cx - spread), int(y1), int(cx), int(y2))
        p.drawLine(int(cx), int(y2), int(cx + spread), int(y1))
    p.end()
    return pm


def chevron_icon(*, down: bool = True, size: int = 14) -> QIcon:
    return QIcon(chevron_pixmap(down=down, size=size))
