"""
pet_window.py - 桌寵主視窗

Windows 版改動
--------------
1. 視窗尺寸改由素材「實際人物範圍」決定（见 animation.py 的 auto_trim），
   確認按鈕出現時才把視窗加高，不再永遠多占 36px 的透明區塊。
2. 定位改用所在螢幕的 availableGeometry（原版座標會壓在工作列下面），
   並在任何移動 / 解析度改變後 clamp，避免桌寵跑到螢幕外再也抓不到。
3. 加上 WindowDoesNotAcceptFocus：點桌寵不會把焦點從終端機搶走。
4. 系統匣：偵測 isSystemTrayAvailable()，並用 showMessage 當原生通知管道
   （搭配 platform_utils 的 AppUserModelID，Windows 10/11 會顯示成 Toast）。
5. 右鍵選單（桌上右鍵 + 系統匣右鍵）、滑鼠穿透、開機自動啟動、
   重新載入素材、模擬通知（沒有 copilot 也能測試）。
6. Linux 原有的互動（點一下 / 摸頭 / 抓起拖曳）完全保留。
"""
import os
import random

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout,
    QApplication, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QPixmap, QIcon, QColor, QCursor, QAction, QFont
from PyQt6.QtCore import QEvent

import config
import platform_utils as pf
from animation import AnimationController, AnimState
from copilot_watcher import CopilotWatcher
from dialog_bubble import DialogBubble
import notifier

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# 互動判定（可在 settings.json 覆寫）
POKE_MAX_MS = int(config.get("poke_max_ms", 250))
PAT_MAX_MS  = int(config.get("pat_max_ms", 800))
GRAB_MS     = int(config.get("grab_ms", 800))

CONFIRM_BTN_H = 30
SCREEN_EDGE_MARGIN = 12


class PetWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._first_show  = True
        self._press_start = 0.0
        self._press_pos   = QPoint()
        self._drag_offset = QPoint()
        self._is_grabbed  = False
        self._click_through = False
        self._tray = None
        self._tray_icon = QIcon()

        # 先載入素材，才知道視窗要多大
        self._init_animation()
        self._init_window()
        self._init_widgets()
        self._start_animation()
        self._init_watcher()
        self._init_tray()
        self._init_timers()

    # ══════════════════════════════════════════════
    # 初始化
    # ══════════════════════════════════════════════

    def _init_animation(self):
        pet_size = tuple(config.get("pet_size", [240, 240]))
        self._anim = AnimationController(ASSETS_DIR, pet_size)
        self._pet_w, self._pet_h = self._anim.display_size
        self._anim.anim_finished.connect(self._on_anim_finished)

    def _pet_label_set_pixmap(self, pixmap):
        if not hasattr(self, "_pet_label"):
            return
        self._pet_label.setPixmap(pixmap)

    def _start_animation(self):
        """
        QLabel 就緒後才接訊號開始播放（否則第一幀會送到不存在的控件）。
        ★ force=True 是必要修正：AnimationController 建立時 current_state 已經是
        IDLE，不帶 force 的 set_state(IDLE) 會直接 return，
        結果第一幀從未送出、計時器也沒啟動 —— 桌寵變成完全透明看不到
        （這個 bug Linux 原版也一樣存在）。
        """
        self._anim.frame_changed.connect(self._pet_label_set_pixmap)
        self._anim.set_state(AnimState.IDLE, force=True)

    def _init_window(self):
        flags = (Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint |
                 Qt.WindowType.Tool |
                 Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle(pf.APP_DISPLAY_NAME)
        self._apply_window_size()

    def _apply_window_size(self):
        extra = CONFIRM_BTN_H + 6 if (hasattr(self, "_confirm_btn")
                                      and self._confirm_btn.isVisible()) else 0
        self.setFixedSize(self._pet_w, self._pet_h + extra)

    def _init_widgets(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._pet_label = QLabel(self)
        self._pet_label.setFixedSize(self._pet_w, self._pet_h)
        self._pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pet_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        layout.addWidget(self._pet_label)

        self._confirm_btn = QPushButton("✓ 確認", self)
        self._confirm_btn.setFixedHeight(CONFIRM_BTN_H)
        font = QFont()
        font.setFamilies(["Microsoft JhengHei", "PingFang TC",
                          "Noto Sans CJK TC", "sans-serif"])
        font.setPixelSize(12)
        font.setBold(True)
        self._confirm_btn.setFont(font)
        self._confirm_btn.setStyleSheet("""
            QPushButton {
                background: rgba(100, 180, 130, 210);
                color: white; border: none; border-radius: 10px;
                font-size: 12px; font-weight: bold;
                padding: 0 8px; margin: 3px 8px;
            }
            QPushButton:hover   { background: rgba(80, 160, 110, 230); }
            QPushButton:pressed { background: rgba(60, 140, 90, 255); }
        """)
        self._confirm_btn.clicked.connect(self._on_confirm)
        self._confirm_btn.hide()
        layout.addWidget(self._confirm_btn)

        self._bubble = DialogBubble()

    def _init_watcher(self):
        self._watcher = CopilotWatcher(self)
        self._watcher.copilot_started.connect(self._on_copilot_started)
        self._watcher.copilot_stopped.connect(self._on_copilot_stopped)
        self._watcher.status_changed.connect(self._on_status_changed)
        self._watcher.start()

    def _init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            # Linux 某些 DE / Windows 極端環境沒有系統匣：
            # 不建立托盤，通知自動退回 notify-send / plyer 通道。
            self._tray = None
            return

        self._tray = QSystemTrayIcon(self)
        self._tray_icon = self._make_tray_icon()
        self._tray.setIcon(self._tray_icon)
        self._tray.setToolTip(self._tooltip())

        # 把系統匣註冊成通知管道（Windows 10/11 會顯示成原生 Toast）
        self._tray_messages_ok = notifier.bind_tray(self._tray, self._tray_icon)

        menu = self._build_menu()
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _make_tray_icon(self) -> QIcon:
        """優先使用第一張 idle 幀（已裁剪 + 縮放），沒有就畫純色圓。"""
        frames = self._anim.frames.get(AnimState.IDLE) or []
        if frames:
            px = frames[0].scaled(32, 32,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            return QIcon(px)
        icon_px = QPixmap(32, 32)
        icon_px.fill(QColor("#A8D8EA"))
        return QIcon(icon_px)

    def _tooltip(self) -> str:
        target = os.path.basename(self._watcher.target) if hasattr(self, "_watcher") else "copilot"
        return f"{pf.APP_DISPLAY_NAME} 🐾｜監聽 {target}"

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        font = QFont()
        font.setFamilies(["Microsoft JhengHei", "PingFang TC",
                          "Noto Sans CJK TC", "sans-serif"])
        font.setPixelSize(13)
        menu.setFont(font)

        act = QAction("顯示 / 隱藏桌寵", self)
        act.triggered.connect(self._toggle_visible)
        menu.addAction(act)

        self._act_click_through = QAction("滑鼠穿透（不挡點擊）", self)
        self._act_click_through.setCheckable(True)
        self._act_click_through.setChecked(self._click_through)
        self._act_click_through.triggered.connect(self._set_click_through)
        menu.addAction(self._act_click_through)

        self._act_autostart = QAction("開機自動啟動", self)
        self._act_autostart.setCheckable(True)
        self._act_autostart.setChecked(pf.autostart_enabled())
        self._act_autostart.triggered.connect(self._toggle_autostart)
        menu.addAction(self._act_autostart)

        menu.addSeparator()

        act = QAction("模擬一次完成通知", self)
        act.setToolTip("沒有在跑 copilot 時，用來測試通知與動畫")
        act.triggered.connect(self._simulate_done)
        menu.addAction(act)

        act = QAction("重新載入素材", self)
        act.setToolTip("換完 assets/ 裡的圖片後不用重啟")
        act.triggered.connect(self._reload_assets)
        menu.addAction(act)

        menu.addSeparator()
        quit_act = QAction("結束", self)
        quit_act.triggered.connect(QApplication.quit)
        menu.addAction(quit_act)
        return menu

    def _init_timers(self):
        self._grab_timer = QTimer(self)
        self._grab_timer.setSingleShot(True)
        self._grab_timer.timeout.connect(self._trigger_grab)

        self._confirm_hide_timer = QTimer(self)
        self._confirm_hide_timer.setSingleShot(True)
        self._confirm_hide_timer.timeout.connect(self._hide_confirm_btn)

    # ══════════════════════════════════════════════
    # 顯示
    # ══════════════════════════════════════════════

    def _show_pet(self):
        if self._first_show:
            self._first_show = False
            self._move_to_default_spot()
        self.show()
        self.raise_()
        self._clamp_position()
        self._update_bubble_pos()

    def _move_to_default_spot(self):
        """預設放在工作區右下角（避開工作列 / Dock）。"""
        avail = pf.available_geometry()
        self.move(avail.right() - self.width() - SCREEN_EDGE_MARGIN,
                  avail.bottom() - self.height() - SCREEN_EDGE_MARGIN)

    def _clamp_position(self):
        fixed = pf.clamp_into_desktop(self.pos(), self.size())
        if fixed != self.pos():
            self.move(fixed)

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
            self._bubble.hide()
        else:
            self._show_pet()

    # ══════════════════════════════════════════════
    # 滑鼠互動
    # ══════════════════════════════════════════════

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        import time
        self._press_start = time.time() * 1000
        self._press_pos   = event.globalPosition().toPoint()
        self._drag_offset = event.globalPosition().toPoint() - self.pos()
        self._grab_timer.start(GRAB_MS)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        new_pos = event.globalPosition().toPoint() - self._drag_offset
        self.move(new_pos)
        self._update_bubble_pos()

    def mouseReleaseEvent(self, event):
        import time
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._grab_timer.stop()
        held_ms = time.time() * 1000 - self._press_start

        if self._is_grabbed:
            self._is_grabbed = False
            self._anim.set_state(AnimState.IDLE)
            self._bubble.show_message("放我下來了～", 1500)
            self._clamp_position()
            self._update_bubble_pos()
            return

        if held_ms < POKE_MAX_MS:
            self._do_poke()
        elif held_ms < PAT_MAX_MS:
            self._do_pat()

    def contextMenuEvent(self, event):
        """右鍵桌寵 = 同一個選單（Windows 使用者習慣）。"""
        menu = self._build_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _trigger_grab(self):
        self._is_grabbed = True
        self._anim.set_state(AnimState.GRABBED)
        self._bubble.show_message("哇！被抓住了！", 0)
        self._update_bubble_pos()

    def _do_poke(self):
        self._anim.set_state(AnimState.POKE, force=True)
        self._bubble.show_message(
            random.choice(["欸！？", "戳我幹嘛！", "嗚…", "///(〃＞＿＜；〃)"]), 2000)
        self._update_bubble_pos()

    def _do_pat(self):
        self._anim.set_state(AnimState.PAT, force=True)
        self._bubble.show_message(
            random.choice([">///<", "好舒服…", "摸頭摸頭～", "(∪｡∪)｡｡｡zzz"]), 2000)
        self._update_bubble_pos()

    def _on_anim_finished(self, state: str):
        if state == AnimState.CONFIRM:
            self._hide_confirm_btn()

    # ══════════════════════════════════════════════
    # Copilot 狀態
    # ══════════════════════════════════════════════

    def _on_copilot_started(self):
        if self._is_grabbed:
            return
        self._anim.set_state(AnimState.WORKING)
        self._bubble.show_message("Copilot 開始工作了！", 2500)
        self._update_bubble_pos()

    def _on_copilot_stopped(self):
        self._anim.set_state(AnimState.NOTIFY)
        self._bubble.show_message("✅ 完成啦！", 4000)
        self._update_bubble_pos()
        notifier.notify_copilot_done()
        self._show_confirm_btn()

    def _on_status_changed(self, msg: str):
        if self._tray is not None:
            self._tray.setToolTip(f"{pf.APP_DISPLAY_NAME} 🐾｜{msg}")

    # ══════════════════════════════════════════════
    # 確認按鈕（會同步改變視窗高度）
    # ══════════════════════════════════════════════

    def _show_confirm_btn(self):
        if self._click_through:
            self._set_click_through(False)     # 穿透時點不到按鈕
        self._confirm_btn.show()
        self._apply_window_size()
        self._clamp_position()
        self._update_bubble_pos()
        self._confirm_hide_timer.start(int(config.get("confirm_auto_hide_ms", 10000)))

    def _hide_confirm_btn(self):
        if not self._confirm_btn.isVisible():
            return
        self._confirm_btn.hide()
        self._confirm_hide_timer.stop()
        self._apply_window_size()
        self._clamp_position()
        self._update_bubble_pos()

    def _on_confirm(self):
        self._anim.set_state(AnimState.CONFIRM, force=True)
        self._bubble.show_message("確認！(ﾉ>ω<)ﾉ", 2000)
        self._update_bubble_pos()
        self._hide_confirm_btn()

    # ══════════════════════════════════════════════
    # 選單動作
    # ══════════════════════════════════════════════

    def _set_click_through(self, on: bool):
        """
        Windows: Qt::WindowTransparentForInput 會加上 WS_EX_TRANSPARENT，
        滑鼠可以直接穿過桌寵點到下面的視窗（不是只讓 Qt 忽略事件）。
        ★ 改旗標必須重新 show 一次才會生效。而且要注意順序：
          Qt 的 setWindowFlag() 自己就會先把視窗 hide 掉，
          所以「原本是否可見」一定要在呼叫 setWindowFlag 之前問，
          否則永遠問到 False、桌寵切完就消失（實測踩過的坑）。
        """
        self._click_through = bool(on)
        was_visible = self.isVisible()
        old_pos = self.pos()
        try:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self._click_through)
            if was_visible:
                self.show()
                self.raise_()
                # 原生視窗重建後位置可能被重置，拉回原本的位置
                if self.pos() != old_pos:
                    self.move(old_pos)
        except Exception as exc:
            print(f"[pet] 切換滑鼠穿透失敗：{exc}")
            self._click_through = False
        if hasattr(self, "_act_click_through"):
            self._act_click_through.setChecked(self._click_through)
        if self._click_through:
            self._bubble.show_message("開啟穿透：點不到我了，從選單關閉", 2500)
        self._update_bubble_pos()

    def _toggle_autostart(self, checked: bool):
        ok, msg = pf.autostart_set(checked)
        if hasattr(self, "_act_autostart"):
            self._act_autostart.setChecked(pf.autostart_enabled())
        if ok:
            self._bubble.show_message(msg, 2500)
            self._update_bubble_pos()
        else:
            notifier.notify("設定失敗", msg)

    def _simulate_done(self):
        """沒有跑 copilot 時也能驗證通知/動畫鏈路。"""
        self._anim.set_state(AnimState.WORKING)
        self._bubble.show_message("模擬工作中…", 1800)
        self._update_bubble_pos()
        QTimer.singleShot(1800, self._on_copilot_stopped)

    def _reload_assets(self):
        old = self._anim
        old.stop()
        self._anim = AnimationController(ASSETS_DIR, tuple(config.get("pet_size", [240, 240])))
        self._pet_w, self._pet_h = self._anim.display_size
        self._anim.frame_changed.connect(self._pet_label_set_pixmap)
        self._anim.anim_finished.connect(self._on_anim_finished)
        self._pet_label.setFixedSize(self._pet_w, self._pet_h)
        self._apply_window_size()
        self._clamp_position()
        self._anim.set_state(AnimState.IDLE, force=True)
        if self._tray is not None:
            self._tray.setIcon(self._make_tray_icon())
        total = self._anim.frame_count()
        self._bubble.show_message(f"重新載入 {total} 張幀", 2000)
        self._update_bubble_pos()
        old.deleteLater()

    # ══════════════════════════════════════════════
    # 通知
    # ══════════════════════════════════════════════

    # 通知實際发送已由 notifier.bind_tray() 接管，
    # 這裡只留一個給診斷用的巨標。
    def tray_messages_supported(self) -> bool:
        return bool(getattr(self, "_tray_messages_ok", False))

    # ══════════════════════════════════════════════
    # 工具 / 事件
    # ══════════════════════════════════════════════

    def _update_bubble_pos(self):
        self._bubble.update_position(self.pos(), (self._pet_w, self._pet_h))

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visible()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            # 選單已由 setContextMenu 處理；這裡保留給 mac/Linux 差異
            pass

    def changeEvent(self, event):
        # 解析度 / DPI 縮放改變後，把桌寵拉回可見範圍
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            QTimer.singleShot(0, self._clamp_position)
        super().changeEvent(event)

    def paintEvent(self, event):
        pass

    def closeEvent(self, event):
        self._bubble.close()
        self._watcher.stop()
        self._anim.stop()
        notifier.unbind_tray()        # 托盤快銷毀了，別再往它送通知
        QApplication.quit()
