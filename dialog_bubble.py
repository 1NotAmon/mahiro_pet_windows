"""
dialog_bubble.py - 對話框泡泡
跟著桌寵位置顯示的無邊框狀態泡泡

Windows 版改動
--------------
1. Qt.WindowType.X11BypassWindowManagerHint 是 X11 限定旗標，
   在 Windows 上無意義，改成只在 Linux 加上（維持原版行為）。
2. 加上 WindowDoesNotAcceptFocus：Windows 上氣泡出現時
   不會把焦點從你正在使用的終端機搶走。
3. 字體指定 CJK fallback 清單（Windows: 微軟正黑體 / macOS: PingFang /
   Linux: Noto Sans CJK），避免中文變成方框。
4. 定位改用「桌寵所在螢幕」的 availableGeometry，並支援
   上方空間不夠時改顯示在下方（原版會被螢幕頂端切掉）。
"""
from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QFont, QPen

import config
from platform_utils import IS_LINUX, available_geometry

# 各平台可用的中文字型（Qt 會自動往下列表 fallback）
FONT_FAMILIES = [
    "Microsoft JhengHei",   # Windows 正黑
    "PingFang TC",          # macOS
    "Noto Sans CJK TC",     # Linux
    "Source Han Sans TC",
    "sans-serif",
]


class DialogBubble(QWidget):
    BUBBLE_W = 220
    BUBBLE_H = 74
    TAIL_H = 14      # 尾巴高度
    TAIL_W = 18      # 尾巴底寬
    CORNER_R = 14
    BG_COLOR = QColor(255, 255, 255, 230)
    BORDER_COLOR = QColor(180, 180, 200, 200)
    TEXT_COLOR = QColor(60, 60, 80)

    def __init__(self, parent=None):
        flags = (Qt.WindowType.Tool
                 | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        if IS_LINUX:
            # 原版行為：繞過視窗管理員，避免被工作區切換 / 動畫影響
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        super().__init__(parent, flags)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.BUBBLE_W, self.BUBBLE_H + self.TAIL_H)

        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPixelSize(int(config.get("bubble_font_px", 13)))
        self._label.setFont(font)
        self._label.setStyleSheet(
            f"color: rgb({self.TEXT_COLOR.red()},{self.TEXT_COLOR.green()},"
            f"{self.TEXT_COLOR.blue()}); background: transparent;")

        # 自動隱藏計時器
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self._tail_down = True       # True = 泡泡在桌寵上方（尾巴朝下）
        self._body_top = self.TAIL_H  # 主體在 widget 內的起點 y
        self._apply_layout()
        self.hide()

    # ── 對外 API ────────────────────────────────────────────
    def show_message(self, text: str, duration_ms: int = 3000):
        """顯示訊息，duration_ms 後自動消失（0 = 不自動消失）"""
        self._label.setText(text)
        self._hide_timer.stop()
        self.show()
        self.raise_()
        if duration_ms > 0:
            self._hide_timer.start(duration_ms)

    def update_position(self, pet_pos: QPoint, pet_size: tuple):
        """根據桌寵位置更新泡泡位置（優先顯示在桌寵上方）"""
        pet_w, pet_h = pet_size
        x = pet_pos.x() + pet_w // 2 - self.BUBBLE_W // 2

        avail = available_geometry(QPoint(pet_pos.x() + pet_w // 2,
                                          pet_pos.y() + pet_h // 2))

        above = pet_pos.y() - self.height() - 4
        tail_down = above >= avail.top()
        if tail_down:
            y = above
        else:
            # 上方放不下 → 改放桌寵下方，尾巴朝上
            y = min(pet_pos.y() + pet_h + 4, avail.bottom() - self.height() + 1)

        if tail_down != self._tail_down:
            self._tail_down = tail_down
            self._apply_layout()
            self.update()

        x = max(avail.left(), min(x, avail.right() - self.BUBBLE_W + 1))
        y = max(avail.top(), min(y, avail.bottom() - self.height() + 1))
        self.move(x, y)

    def hideEvent(self, event):
        self._hide_timer.stop()
        super().hideEvent(event)

    # ── 內部排版 ────────────────────────────────────────────
    def _apply_layout(self):
        """依尾巴方向決定主體起點，並同步標籤位置。"""
        self._body_top = 0 if self._tail_down else self.TAIL_H
        self._label.setGeometry(12, self._body_top + 8,
                                self.BUBBLE_W - 24, self.BUBBLE_H - 16)

    def _body_path(self) -> QPainterPath:
        w, h, r = self.BUBBLE_W, self.BUBBLE_H, self.CORNER_R
        bt = self._body_top
        tail_cx, tail_w, tail_h = w // 2, self.TAIL_W, self.TAIL_H

        path = QPainterPath()
        path.moveTo(r, bt)
        if not self._tail_down:
            # 頂邊中間插入朝上的尾巴
            path.lineTo(tail_cx - tail_w // 2, bt)
            path.lineTo(tail_cx, bt - tail_h)
            path.lineTo(tail_cx + tail_w // 2, bt)
        path.lineTo(w - r, bt)
        path.quadTo(w, bt, w, bt + r)
        path.lineTo(w, bt + h - r)
        path.quadTo(w, bt + h, w - r, bt + h)
        if self._tail_down:
            # 底邊中間插入朝下的尾巴
            path.lineTo(tail_cx + tail_w // 2, bt + h)
            path.lineTo(tail_cx, bt + h + tail_h)
            path.lineTo(tail_cx - tail_w // 2, bt + h)
        path.lineTo(r, bt + h)
        path.quadTo(0, bt + h, 0, bt + h - r)
        path.lineTo(0, bt + r)
        path.quadTo(0, bt, r, bt)
        path.closeSubpath()
        return path

    # ── 繪製 ────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = self._body_path()

        # 陰影
        shadow_path = QPainterPath(path)
        shadow_path.translate(2, 2)
        painter.fillPath(shadow_path, QColor(0, 0, 0, 30))

        # 主體
        painter.fillPath(path, self.BG_COLOR)

        # 邊框
        painter.setPen(QPen(self.BORDER_COLOR, 1.5))
        painter.drawPath(path)
