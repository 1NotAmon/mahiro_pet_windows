#!/usr/bin/env python3
"""
Desktop Pet / 真尋桌寵 - 主程式入口
跨平台版本（Windows / Linux / macOS）

啟動方式
--------
Windows :  雙擊「啟動桌寵.bat」，或在終端機執行  .venv\\Scripts\\python.exe main.py
Linux   :  bash setup.sh  或  python3 main.py

命令列參數
----------
--hidden             啟動時不顯示桌寵（只在系統匣）
--test-notify        只發一次測試通知後結束（驗證系統通知是否正常）
--autostart on|off   設定 / 取消開機自動啟動
--info               印出環境資訊（Python / Qt / copilot 路徑）後結束
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ★ 必須在 import PyQt6 之前呼叫：
#   - Windows 主控台編碼轉 UTF-8（否則 print 中文會 UnicodeEncodeError）
#   - 只在 Linux 設定 QT_QPA_PLATFORM=xcb（Windows 塞 xcb 會直接啟動失敗）
#   - Windows 註冊 AppUserModelID（否則系統匣 Toast 通知不會顯示）
from platform_utils import (IS_WINDOWS, configure_runtime, message_box,
                            find_copilot, install_guide_text, autostart_set,
                            autostart_enabled, APP_DISPLAY_NAME)

configure_runtime()

# ── PyQt6 缺件時給「看得懂」的錯誤，而不是 traceback ──────────
try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
    from PyQt6.QtGui import QIcon
    from PyQt6.QtCore import QTimer
except ImportError as exc:
    guide = (
        f"缺少 PyQt6（{exc}）。\n\n"
        "Windows 請先執行：\n"
        "  powershell -ExecutionPolicy Bypass -File setup.ps1\n\n"
        "Linux 請先執行：\n"
        "  bash setup.sh\n\n"
        "或手動安裝：\n"
        "  python -m pip install -r requirements.txt"
    )
    message_box(f"{APP_DISPLAY_NAME} - 無法啟動", guide, "error")
    sys.exit(1)

import config
import notifier


# ══════════════════════════════════════════════════════════
# 依賴檢查
# ══════════════════════════════════════════════════════════

def check_dependencies():
    """檢查 Python 套件。psutil 是必須的；plyer 只是通知 fallback。"""
    missing = []
    try:
        import psutil          # noqa: F401
    except ImportError:
        missing.append("psutil")
    try:
        import plyer           # noqa: F401
    except ImportError:
        # Windows 走系統匣原生通知，沒有 plyer 也能正常用
        if not IS_WINDOWS:
            missing.append("plyer")
    return missing


def show_missing_deps(app, missing):
    """缺少套件時顯示安裝提示，可自動安裝"""
    pkgs = " ".join(missing)
    msg = QMessageBox()
    msg.setWindowTitle("缺少套件")
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText(f"缺少必要的 Python 套件：{pkgs}")
    msg.setInformativeText(
        f"請執行以下指令安裝：\n\n"
        f"  {sys.executable} -m pip install {pkgs}\n\n"
        f"或點「自動安裝」讓我幫你裝。"
    )
    install_btn = msg.addButton("自動安裝", QMessageBox.ButtonRole.AcceptRole)
    msg.addButton("關閉", QMessageBox.ButtonRole.RejectRole)
    msg.exec()

    if msg.clickedButton() == install_btn:
        import subprocess
        try:
            ret = subprocess.run(
                [sys.executable, "-m", "pip", "install"] + missing,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if ret.returncode == 0:
                QMessageBox.information(None, "安裝完成", "套件安裝成功！\n請重新啟動桌寵。")
            else:
                QMessageBox.critical(None, "安裝失敗",
                                     f"請手動執行：\npip install {pkgs}\n\n{ret.stderr}")
        except Exception as e:
            QMessageBox.critical(None, "安裝失敗", str(e))
    sys.exit(1)


# ══════════════════════════════════════════════════════════
# Copilot 檢查
# ══════════════════════════════════════════════════════════

def check_copilot():
    """找 copilot 執行檔（Windows / Linux / macOS），找不到回傳 None"""
    configured = config.watcher("copilot_path")
    if configured:
        expanded = os.path.expanduser(configured)
        if os.path.exists(expanded):
            return os.path.realpath(expanded)
    return find_copilot()


def show_install_guide(app):
    """找不到 copilot 時顯示安裝提示"""
    msg = QMessageBox()
    msg.setWindowTitle("找不到 GitHub Copilot CLI")
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setText("找不到 copilot 指令！")
    msg.setInformativeText(
        install_guide_text() +
        "\n\n找不到也沒關係：動畫、摸頭 / 抓起等互動都能正常使用，"
        "只是不會自動偵測 Copilot 的工作狀態。"
    )
    btn_ignore = msg.addButton("先跳過，繼續啟動", QMessageBox.ButtonRole.AcceptRole)
    msg.addButton("關閉", QMessageBox.ButtonRole.RejectRole)
    msg.exec()
    return msg.clickedButton() == btn_ignore


# ══════════════════════════════════════════════════════════
# 單實例保護（避免雙擊兩次冒出兩隻桌寵）
# ══════════════════════════════════════════════════════════

_instance_handle = None          # 保持參考，進程結束時由 OS 釋放


def acquire_single_instance() -> bool:
    global _instance_handle
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, False, "Local\\ZhenXunDesktopPet")
            ERROR_ALREADY_EXISTS = 183
            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                return False
            _instance_handle = handle
            return True
        except Exception:
            return True

    # Linux / macOS：lock file + flock
    try:
        import fcntl
        lock_path = os.path.join(os.path.expanduser("~"), ".zhenxun_desktop_pet.lock")
        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        fh.flush()
        _instance_handle = fh
        return True
    except (OSError, ImportError):
        return True


# ══════════════════════════════════════════════════════════
# 不需要桌寵本體的命令列參數
# ══════════════════════════════════════════════════════════

def _print_info() -> int:
    from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
    print(f"Python      : {sys.version.split()[0]} ({sys.executable})")
    print(f"平台        : {IS_WINDOWS and 'Windows' or sys.platform}")
    print(f"Qt / PyQt   : {QT_VERSION_STR} / {PYQT_VERSION_STR}")
    print(f"Copilot     : {check_copilot() or '（找不到）'}")
    print(f"開機自啟    : {'已開啟' if autostart_enabled() else '未開啟'}")
    print(f"系統匣可用  : {QSystemTrayIcon.isSystemTrayAvailable()}")
    try:
        import PIL                    # noqa: F401
        print("Pillow      : 已安裝（支援自動裁剪透明邊）")
    except ImportError:
        print("Pillow      : 未安裝（跳過透明邊裁剪）")
    print(f"settings    : {config.CONFIG_FILE}"
          f"（{'存在' if os.path.exists(config.CONFIG_FILE) else '未建立，使用預設值'}）")
    return 0


def _test_notify(app) -> int:
    tray = QSystemTrayIcon(app)
    icon_path = os.path.join(BASE_DIR, "assets", "idle", "001.png")
    icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
    tray.setIcon(icon)
    tray.show()
    print(f"系統匣可用：{QSystemTrayIcon.isSystemTrayAvailable()}"
          f"｜支援氣球訊息：{tray.supportsMessages()}")
    # ★ 一定要綁定，否則 notify() 找不到管道會默默退回 stderr
    notifier.bind_tray(tray, icon)
    result = notifier.notify(f"{APP_DISPLAY_NAME} 測試通知",
                             "看到這條就代表系統通知正常 ✅")
    print(f"通知送出：{result}")
    print("（Toast 通常出現在螢幕右下角，會被工作列通知中心收錄）")
    QTimer.singleShot(2000, app.quit)     # 讓 Toast 有時間進佇列
    return 0 if result else 1


def handle_cli_shortcuts(app):
    """回傳 int 表示直接以該 exit code 結束；回傳 None 表示繼續正常啟動。"""
    args = sys.argv[1:]

    if "--info" in args:
        return _print_info()

    if "--autostart" in args:
        idx = args.index("--autostart")
        value = args[idx + 1].lower() if idx + 1 < len(args) else "on"
        enable = value in ("on", "true", "1", "yes", "enable")
        ok, msg = autostart_set(enable)
        print(f"{'OK' if ok else 'FAIL'}: {msg}")
        print(f"目前狀態：{'已開啟' if autostart_enabled() else '已關閉'}")
        return 0 if ok else 1

    if "--test-notify" in args:
        return _test_notify(app)

    return None


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName("ZhenXun")
    app.setQuitOnLastWindowClosed(False)

    # 0. 診斷 / 設定類參數
    shortcut_code = handle_cli_shortcuts(app)
    if shortcut_code is not None:
        sys.exit(shortcut_code)

    # 1. 單實例
    if not acquire_single_instance():
        message_box(APP_DISPLAY_NAME,
                    "桌寵已經在運行了（看右下角系統匣）。\n"
                    "如果要重新啟動，請先在系統匣選「結束」。", "info")
        sys.exit(0)

    # 2. 檢查 Python 依賴
    missing = check_dependencies()
    if missing:
        show_missing_deps(app, missing)
        return

    # 3. 檢查 copilot（找不到可以跳過）
    copilot_path = check_copilot()
    if copilot_path:
        print(f"[pet] 監聽 Copilot：{copilot_path}")
    else:
        if not show_install_guide(app):
            sys.exit(0)

    # 4. 啟動桌寵
    from pet_window import PetWindow
    window = PetWindow()
    hidden = ("--hidden" in sys.argv[1:]) or bool(config.get("start_hidden", False))
    if hidden:
        print("[pet] 以隱藏模式啟動（只在系統匣）")
    else:
        window._show_pet()

    print(f"[pet] {APP_DISPLAY_NAME} 已啟動｜"
          f"素材 {window._anim.frame_count()} 張幀｜"
          f"視窗 {window._pet_w}x{window._pet_h}")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
