"""
animation.py - 動畫控制器
管理所有動作的幀動畫切換

Windows 版改動
--------------
1. 原本隨处在用 __import__("PyQt6.QtCore"...)" 取 Qt，改成頂部正規 import。
2. 幀檔改用「自然排序」（002 不會排在 010 後面）。
3. 【啟動速度】原本 90 張 1024x1024 全部在主執行緒同步解碼，
   Windows 上實測約 3.5 秒才會出現桌寵。改成：
   先同步載入 idle（立刻能顯示），其餘狀態在背景執行緒解碼，
   完成一個狀態就用訊號回報主執行緒（QImage → QPixmap 的轉換一律在
   主執行緒，符合 Qt「QPixmap 只能在 GUI 執行緒建立」的規則）。
4. 【可選】auto_trim：依 alpha 自動裁掉素材四周的透明邊。
   人物視覺大小不變（用同一個縮放比例換算），但視窗會貼著人物，
   較不會挡到桌面後面的圖示。素材本来就貼滿畫布時效益有限，
   所以預設關閉；要開就在 settings.json 設 "auto_trim": true。
5. FPS 可由 settings.json 覆寫。
"""
import os
import re
import time
import threading

from PyQt6.QtCore import Qt, QRect, QSize, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage, QColor, QPainter, QPen, QFont

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:                                  # 沒裝 Pillow：跳過裁剪，改用 Qt 解碼
    HAS_PIL = False

import config

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# 透明邊分析最多取樣幾張（全掃會多花 2~4 秒）
TRIM_SAMPLE_LIMIT = 12
TRIM_PAD = 8            # 取樣可能有漏，多留一點邊界


# 動畫狀態定義
class AnimState:
    IDLE = "idle"
    WORKING = "working"
    PAT = "pat"
    GRABBED = "grabbed"
    POKE = "poke"
    NOTIFY = "notify"
    CONFIRM = "confirm"


ALL_STATES = [
    AnimState.IDLE, AnimState.WORKING, AnimState.PAT,
    AnimState.GRABBED, AnimState.POKE, AnimState.NOTIFY, AnimState.CONFIRM,
]

# 各動畫播放設定
ANIM_CONFIG = {
    AnimState.IDLE:    {"fps": 8,  "loop": True,  "next": None},
    AnimState.WORKING: {"fps": 10, "loop": True,  "next": None},
    AnimState.PAT:     {"fps": 10, "loop": False, "next": AnimState.IDLE},
    AnimState.GRABBED: {"fps": 8,  "loop": True,  "next": None},
    AnimState.POKE:    {"fps": 12, "loop": False, "next": AnimState.IDLE},
    AnimState.NOTIFY:  {"fps": 10, "loop": False, "next": AnimState.IDLE},
    AnimState.CONFIRM: {"fps": 10, "loop": False, "next": AnimState.IDLE},
}

# 預設佔位圖顏色（當圖片不存在時使用）
PLACEHOLDER_COLORS = {
    AnimState.IDLE:    "#A8D8EA",
    AnimState.WORKING: "#FFD3B6",
    AnimState.PAT:     "#FFAAA5",
    AnimState.GRABBED: "#D4A5A5",
    AnimState.POKE:    "#B5EAD7",
    AnimState.NOTIFY:  "#FFDAC1",
    AnimState.CONFIRM: "#C7CEEA",
}


def natural_key(name: str):
    """001.png / 002.png / 010.png 依數字大小排序，而不是字串順序。"""
    parts = re.split(r"(\d+)", name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


class AnimationController(QObject):
    frame_changed = pyqtSignal(QPixmap)
    anim_finished = pyqtSignal(str)      # 動畫播放完畢時發出，帶狀態名
    state_ready   = pyqtSignal(str)      # 背景載入完成一個狀態

    def __init__(self, assets_dir: str, pet_size: tuple = (120, 120)):
        super().__init__()
        self.assets_dir = assets_dir
        self.canvas_size = (int(pet_size[0]), int(pet_size[1]))
        self.current_state = AnimState.IDLE
        self.frames: dict[str, list[QPixmap]] = {s: [] for s in ALL_STATES}
        self.current_frame_idx = 0
        self.placeholder_pixmaps: dict[str, QPixmap] = {}
        self.display_size = self.canvas_size
        self.trim_rect = None
        self.loading_done = False

        self._paths: dict[str, list[str]] = {s: [] for s in ALL_STATES}
        self._pending: dict[str, list[QImage]] = {}
        # 還在前背景執行緒解碼的狀態名；還沒好之前不要判定「播完」
        self._loading_states: set[str] = set()
        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._thread = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_frame)
        self.state_ready.connect(self._on_state_ready)

        self._prepare()
        self._make_placeholders()
        self._load_state_sync(AnimState.IDLE)     # 立刻有東西可顯示
        self._start_background_load()

    # ══════════════════════════════════════════════
    # 掃描與裁剪分析
    # ══════════════════════════════════════════════

    def _prepare(self):
        self._paths = self._scan_source_files()
        all_paths = [p for lst in self._paths.values() for p in lst]

        trim_info = None
        if HAS_PIL and config.get("auto_trim", False):
            trim_info = self._compute_trim_rect(all_paths)
        if trim_info:
            self.trim_rect, canvas_size = trim_info
            # 維持原本的「人物視覺大小」：同一個縮放比例套在裁剪範圍上
            scale = min(self.canvas_size[0] / canvas_size[0],
                        self.canvas_size[1] / canvas_size[1])
            w = max(32, round(self.trim_rect.width() * scale))
            h = max(32, round(self.trim_rect.height() * scale))
            self.display_size = (w, h)
        else:
            self.display_size = self.canvas_size

    def _scan_source_files(self):
        """只掃描檔案，先不解碼。"""
        paths = {}
        for state in ALL_STATES:
            folder = os.path.join(self.assets_dir, state)
            found = []
            if os.path.isdir(folder):
                files = [f for f in os.listdir(folder)
                         if f.lower().endswith(IMAGE_EXTS)]
                files.sort(key=natural_key)
                found = [os.path.join(folder, f) for f in files]
            paths[state] = found
        return paths

    def _compute_trim_rect(self, all_paths):
        """
        用 alpha 通道找出「所有幀合起來的實際內容範圍」。
        為省時間只取樣最多 TRIM_SAMPLE_LIMIT 張，外層再留 TRIM_PAD 邊界。
        在縮圖上分析（1024x1024 逐像素太慢），再換算回原圖座標。
        """
        if not all_paths:
            return None
        step = max(1, len(all_paths) // TRIM_SAMPLE_LIMIT)
        sample = all_paths[::step][:TRIM_SAMPLE_LIMIT]

        min_x = min_y = 10 ** 9
        max_x = max_y = -1
        canvas_w = canvas_h = 0
        analysed = 0

        for path in sample:
            try:
                with Image.open(path) as raw:
                    raw.load()
                    if canvas_w == 0:
                        canvas_w, canvas_h = raw.size
                    rgba = raw.convert("RGBA")
                    sw = max(1, min(rgba.width, 128))
                    sh = max(1, round(rgba.height * sw / rgba.width))
                    small = rgba.resize((sw, sh))
                    sx = rgba.width / sw
                    sy = rgba.height / sh
                    bbox = small.getchannel("A").getbbox()
            except Exception:
                continue
            if not bbox:
                continue
            l, t, r, b = bbox
            min_x = min(min_x, int(l * sx))
            min_y = min(min_y, int(t * sy))
            max_x = max(max_x, int(r * sx))
            max_y = max(max_y, int(b * sy))
            analysed += 1

        if analysed == 0 or canvas_w == 0 or max_x <= min_x or max_y <= min_y:
            return None
        rect = QRect(max(0, min_x - TRIM_PAD), max(0, min_y - TRIM_PAD),
                     min(canvas_w, max_x + TRIM_PAD) - max(0, min_x - TRIM_PAD),
                     min(canvas_h, max_y + TRIM_PAD) - max(0, min_y - TRIM_PAD))
        # 根本沒縮小（素材已貼滿）就不用裁剪
        if rect.width() >= canvas_w - 4 and rect.height() >= canvas_h - 4:
            return None
        return rect, (canvas_w, canvas_h)

    # ══════════════════════════════════════════════
    # 解碼
    # ══════════════════════════════════════════════

    def _decode_to_qimages(self, paths) -> list:
        """
        在背景執行緒把檔案解成 QImage（QImage 執行緒安全，QPixmap 不安全）。
        有 PIL 就用 PIL：解碼 + 裁剪 + 縮放一次到位，也才能做 alpha 分析。
        """
        w, h = self.display_size
        out = []
        box = None
        if self.trim_rect:
            box = (self.trim_rect.x(), self.trim_rect.y(),
                   self.trim_rect.x() + self.trim_rect.width(),
                   self.trim_rect.y() + self.trim_rect.height())
        for path in paths:
            try:
                if HAS_PIL:
                    with Image.open(path) as raw:
                        raw.load()
                        rgba = raw.convert("RGBA")
                    if box:
                        rgba = rgba.crop(box)
                    if rgba.size != (w, h):
                        rgba = rgba.resize((w, h), Image.LANCZOS)
                    data = rgba.tobytes("raw", "RGBA")
                    qimg = QImage(data, w, h, QImage.Format.Format_RGBA8888)
                    out.append(qimg.copy())         # copy：脫離 data 的生命週期
                else:
                    px = QPixmap(path)
                    if px.isNull():
                        continue
                    if self.trim_rect:
                        px = px.copy(self.trim_rect)
                    out.append(px.toImage().scaled(
                        w, h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
            except Exception:
                continue
        return out

    def _load_state_sync(self, state: str):
        """主執行緒直接載入（只給開機第一幀用，幀數少才划算）。"""
        images = self._decode_to_qimages(self._paths.get(state, []))
        self.frames[state] = [QPixmap.fromImage(img) for img in images]

    def _start_background_load(self):
        """其餘狀態丢給背景執行緒。"""
        todo = [s for s in ALL_STATES if s != AnimState.IDLE and self._paths.get(s)]
        # 此時才掃描過檔案，可以確定哪些狀態「還在路上」
        self._loading_states = set(todo)
        if not todo:
            self.loading_done = True
            return

        def worker():
            for state in todo:
                if self._abort.is_set():
                    return
                images = self._decode_to_qimages(self._paths[state])
                if self._abort.is_set():
                    return
                with self._lock:
                    self._pending[state] = images
                self.state_ready.emit(state)
                time.sleep(0.01)       # 讓出 GIL，別把 UI 餓死
            self.loading_done = True
            # 全部跑完：沒有素材的狀態不再被當成「還在載入」，
            # 否則佔位圖會卡在那裡永遠不會「播完」
            self._loading_states.clear()

        self._thread = threading.Thread(target=worker, daemon=True,
                                        name="pet-frame-loader")
        self._thread.start()

    def _on_state_ready(self, state: str):
        with self._lock:
            images = self._pending.pop(state, None)
        self._loading_states.discard(state)
        if not images:
            return
        self.frames[state] = [QPixmap.fromImage(img) for img in images]
        if state == self.current_state:
            # 幀才剛到：從第一幀重新播，避免半截動畫或立即「播完」
            self.current_frame_idx = 0
            if not self._timer.isActive():
                cfg = ANIM_CONFIG.get(state, {"fps": 8})
                fps = config.fps_for(state, cfg.get("fps", 8))
                self._timer.start(max(1, int(1000 / max(1, fps))))
            self._emit_current_frame()

    def stop(self):
        """停止播放並中止背景載入（重新載入素材／關閉時）。"""
        self._timer.stop()
        self._abort.set()

    def frame_count(self, state: str = None) -> int:
        if state is not None:
            return len(self.frames.get(state, []))
        return sum(len(v) for v in self.frames.values())

    # ══════════════════════════════════════════════
    # 佔位圖（沒圖時的彩色圓圈）
    # ══════════════════════════════════════════════

    def _make_placeholders(self):
        w, h = self.display_size
        w = max(w, 60)
        h = max(h, 60)
        for state, color in PLACEHOLDER_COLORS.items():
            px = QPixmap(w, h)
            px.fill(QColor(0, 0, 0, 0))
            painter = QPainter(px)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(10, 10, w - 20, h - 20)
            # 眼睛
            painter.setBrush(QColor("#333333"))
            painter.drawEllipse(w // 2 - 18, h // 2 - 10, 10, 10)
            painter.drawEllipse(w // 2 + 8, h // 2 - 10, 10, 10)
            # 嘴巴
            painter.setPen(QPen(QColor("#333333"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRect(w // 2 - 12, h // 2, 24, 14), 0, -180 * 16)
            # 狀態標籤
            painter.setPen(QColor("#555555"))
            font = QFont()
            font.setPixelSize(9)
            painter.setFont(font)
            painter.drawText(0, h - 4, w, 12,
                             Qt.AlignmentFlag.AlignHCenter, state)
            painter.end()
            self.placeholder_pixmaps[state] = px

    # ══════════════════════════════════════════════
    # 狀態切換
    # ══════════════════════════════════════════════

    def set_state(self, state: str, force: bool = False):
        """切換到指定動畫狀態"""
        if state == self.current_state and not force:
            return
        self.current_state = state
        self.current_frame_idx = 0
        self._timer.stop()

        cfg = ANIM_CONFIG.get(state, {"fps": 8})
        fps = config.fps_for(state, cfg.get("fps", 8))
        interval = max(1, int(1000 / max(1, fps)))
        self._timer.start(interval)
        self._emit_current_frame()

    def _next_frame(self):
        frames = self.frames.get(self.current_state, [])
        cfg = ANIM_CONFIG.get(self.current_state, {"loop": True, "next": None})
        # 背景還在解碼、目前沒有任何幀 → 先撐住佔位圖，
        # 不然會被當成「只有 1 幀且非循環」而立刻跳回 IDLE
        if not frames and self.current_state in self._loading_states:
            self._emit_current_frame()
            return
        total = len(frames) if frames else 1

        self.current_frame_idx += 1
        if self.current_frame_idx >= total:
            if cfg.get("loop"):
                self.current_frame_idx = 0
            else:
                self.current_frame_idx = total - 1
                self._timer.stop()
                next_state = cfg.get("next")
                self.anim_finished.emit(self.current_state)
                if next_state:
                    self.set_state(next_state)
                return
        self._emit_current_frame()

    def _emit_current_frame(self):
        frames = self.frames.get(self.current_state, [])
        if frames:
            idx = min(self.current_frame_idx, len(frames) - 1)
            px = frames[idx]
        else:
            px = self.placeholder_pixmaps.get(self.current_state,
                                              self.placeholder_pixmaps[AnimState.IDLE])
        self.frame_changed.emit(px)

    @property
    def state(self):
        return self.current_state
