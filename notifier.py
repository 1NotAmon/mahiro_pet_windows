"""
notifier.py - 系統通知
Copilot 完成工作後送出桌面通知

Windows 版改動
--------------
原版只走 notify-send（Linux）。Windows 上沒有 notify-send，
而 plyer 的 Windows 實作在 Python 3.12+ / Windows 11 常常直接失敗
（它依赖舊的 Shell_NotifyIcon 包裝，且需要額外套件）。

所以改成：
1. 優先使用桌寵自己的系統匣（QSystemTrayIcon.showMessage）
   → Windows 10/11 會顯示成原生 Toast，不需要任何額外套件。
   pet_window 啟動時會用 register_notifier() 把這個通道註冊進來。
2. Linux 維持 notify-send。
3. 再退回 plyer。
4. 全部失敗就印到 stderr（不會讓程式 crash）。
"""
import os
import sys
import shutil
import subprocess

from platform_utils import IS_WINDOWS, IS_LINUX, APP_NAME, APP_DISPLAY_NAME

_notifier_hook = None


def register_notifier(fn):
    """
    由 pet_window 註冊實際的通知管道（通常是 Tray.showMessage）。
    fn(title: str, message: str, duration_ms: int) -> bool
    回傳 True 表示已顯示。
    """
    global _notifier_hook
    _notifier_hook = fn


def bind_tray(tray, icon=None) -> bool:
    """
    把一個 QSystemTrayIcon 綁成通知管道（PetWindow 與 --test-notify 共用）。
    回傳是否綁上（沒有系統匣或不支援氣球訊息時回 False，
    notify() 會自動改用其他平台管道）。
    """
    if tray is None:
        return False
    try:
        if not tray.supportsMessages():
            return False
    except Exception:
        return False

    def hook(title: str, message: str, duration_ms: int = 5000) -> bool:
        try:
            timeout = max(1000, int(duration_ms or 5000))
            try:
                tray.showMessage(title, message, icon, timeout)
            except TypeError:
                # 某些 PyQt6 版本只有 (title, message) 載具
                tray.showMessage(title, message)
            except Exception:
                tray.showMessage(title, message)
            return True
        except Exception:
            return False

    register_notifier(hook)
    return True


def unbind_tray():
    """結束時解除綁定，避免通知送到已經銷毀的托盤物件。"""
    global _notifier_hook
    _notifier_hook = None


def play_done_sound():
    """完成提示音：Windows 用系統音效，其他平台靜默跳過。"""
    if not IS_WINDOWS:
        return
    try:
        import winsound
        winsound.PlaySound("SystemAsterisk",
                           winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


def notify(title: str, message: str, icon: str = "dialog-information",
           duration_ms: int = 5000):
    """送出系統通知（自動挑選目前平台可用的方式）。"""
    # 1) 應用內系統匣（Windows / Linux 都適用，也最可靠）
    if _notifier_hook is not None:
        try:
            if _notifier_hook(title, message, duration_ms):
                return True
        except Exception:
            pass

    # 2) Linux: notify-send
    if IS_LINUX and shutil.which("notify-send"):
        try:
            subprocess.Popen(
                ["notify-send", "--app-name", APP_NAME,
                 "--icon", icon, "--urgency", "normal", title, message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            pass

    # 3) macOS: osascript（順帶支援，作者若在 Mac 也能跑）
    if sys.platform == "darwin":
        try:
            script = f'display notification {json_escape(message)} with title {json_escape(title)}'
            subprocess.Popen(["osascript", "-e", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass

    # 4) plyer fallback
    try:
        from plyer import notification
        notification.notify(title=title, message=message,
                            app_name=APP_NAME, timeout=max(1, duration_ms // 1000))
        return True
    except Exception:
        pass

    # 5) 最後手段：console
    print(f"[通知] {title}: {message}", file=sys.stderr)
    return False


def json_escape(text: str) -> str:
    """給 osascript 用的字串轉義。"""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify_copilot_done():
    ok = notify(
        f"✅ {APP_DISPLAY_NAME}｜Copilot 完成了！",
        "GitHub Copilot CLI 已完成工作",
        icon="emblem-ok-symbolic",
    )
    if ok:
        play_done_sound()
    return ok


def notify_copilot_started():
    return notify(
        f"🤖 {APP_DISPLAY_NAME}｜Copilot 開始工作",
        "GitHub Copilot CLI 正在執行中",
        icon="media-playback-start-symbolic",
    )
