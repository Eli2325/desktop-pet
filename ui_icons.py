"""共享小图标：控制台展开箭头、QSS 用 SVG 下拉箭头等。"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

# QComboBox::down-arrow 用（避免 border 三角形在高 DPI 下缩成「横条」）
# stroke #334155 → URL 编码 %23334155
COMBO_CHEVRON_DOWN_QSS = (
    "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    "width='16' height='16' viewBox='0 0 16 16'%3E%3Cpath fill='none' "
    "stroke='%23334155' stroke-width='1.75' stroke-linecap='round' "
    "stroke-linejoin='round' d='M4 6l4 4 4-4'/%3E%3C/svg%3E\")"
)


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
