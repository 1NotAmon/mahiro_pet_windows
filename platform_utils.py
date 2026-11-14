"""
platform_utils.py - 跨平台相容層（Windows / Linux / macOS）

原版只在 Linux/X11 跑，平台相關的假設都散在各部门裡。
這個模組把它們集中起來，其他模組只要問這裡，
這樣 Linux 的行為不會被破壞，Windows 也能直接用。
"""
import os
import sys
import shutil

IS_WINDOWS = (os.name == "nt")
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

APP_NAME = "Desktop Pet"
APP_DISPLAY_NAME = "真尋桌寵"

# Windows 的通知（Toast）需要明確的 AppUserModelID，
# 否則系統匣氣球通知可能完全不顯示、或標題變成 python.exe。
APP_USER_MODEL_ID = "com.zhenxun.desktoppet"


# ══════════════════════════════════════════════════════════
# 執行期設定（要在建立 QApplication 之前呼叫）
# ══════════════════════════════════════════════════════════

def configure_runtime():
    """統一處理編碼、Qt 平台插件、Windows AUMID。"""
    _fix_console_encoding()

    # Qt 平台插件：只有 Linux 需要強制 xcb。
    # Windows 必須使用 Qt 的 windows 插件，
    # 硬塞 xcb 會直接啟動失敗（could not load platform plugin "xcb"）。
    if IS_LINUX:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    if IS_WINDOWS:
        set_app_user_model_id()


def _fix_console_encoding():
    """
    Windows 主控台預設用 OEM 編碼（Big5/cp950 或 cp437），
    print 中文或 emoji 會拋 UnicodeEncodeError 讓程式掛掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def set_app_user_model_id(aumid: str = APP_USER_MODEL_ID):
    """Windows：讓系統匣 / Toast 通知正確歸屬到本程式。"""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
    except Exception:
        pass


def message_box(title: str, text: str, kind: str = "warning"):
    """
    在沒有 PyQt6（或還沒建立 QApplication）時的提示方案：
    Windows 用 MessageBoxW，Linux 試 zenity/kdialog，其餘印到 stderr。
    """
    if IS_WINDOWS:
        try:
            import ctypes
            MB_ICON = {"info": 0x40, "warning": 0x30, "error": 0x10}.get(kind, 0x30)
            MB_YESNO = 0x04
            MB_OK = 0x00
            MB_TOPMOST = 0x40000
            return ctypes.windll.user32.MessageBoxW(0, text, title, MB_ICON | MB_OK | MB_TOPMOST)
        except Exception:
            pass
    else:
        for tool, extra in (("zenity", ["--warning", "--title"]),
                            ("kdialog", ["--title"])):
            exe = shutil.which(tool)
            if exe:
                try:
                    import subprocess
                    if tool == "zenity":
                        subprocess.Popen([exe, "--warning", "--title", title,
                                          "--text", text],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                    else:
                        subprocess.Popen([exe, "--title", title, text.replace("\n", " ")],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                    return 0
                except Exception:
                    pass
    print(f"[{title}]\n{text}", file=sys.stderr)
    return 0


# ══════════════════════════════════════════════════════════
# Copilot CLI 位置
# ══════════════════════════════════════════════════════════

def copilot_candidate_paths():
    """各平台常見安裝位置（PATH 之外）。"""
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        roaming = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        names = ("copilot.exe", "copilot.cmd", "copilot.bat", "copilot")
        roots = [
            os.path.join(local, "GitHubCopilotCLI"),      # winget / 官方安裝腳本預設位置
            os.path.join(local, "Programs", "copilot"),
            os.path.join(roaming, "npm"),                 # npm install -g
            os.path.join(home, ".local", "bin"),
            os.path.join(program_files, "GitHub Copilot CLI"),
            os.path.join(program_files, "copilot"),
        ]
    elif IS_MACOS:
        names = ("copilot",)
        roots = ["/opt/homebrew/bin", "/usr/local/bin", os.path.join(home, ".local", "bin")]
    else:  # Linux（維持原版行為）
        names = ("copilot",)
        roots = [os.path.join(home, ".local", "bin"),
                 "/usr/local/bin", "/usr/bin", "/snap/bin"]

    out = []
    for root in roots:
        for name in names:
            p = os.path.join(root, name)
            if os.path.exists(p):
                out.append(p)
    return out


def find_copilot():
    """找 copilot 執行檔，找不到回傳 None。"""
    found = shutil.which("copilot")
    if found:
        return found
    cands = copilot_candidate_paths()
    return cands[0] if cands else None


def copilot_match_names(exe_path: str = None):
    """
    比對用的「程序名稱」集合。
    Windows 上執行檔叫 copilot.exe，Linux 上叫 copilot；
    另外把 npm 的 cmd 包裝器名稱也納入。
    """
    names = {"copilot", "copilot.exe", "copilot.cmd", "copilot.bat",
             "github-copilot", "github-copilot.exe"}
    if exe_path:
        names.add(os.path.basename(exe_path).lower())
    return names


def install_guide_text():
    """找不到 copilot 時顯示的安裝說明（依平台）。"""
    if IS_WINDOWS:
        return (
            "請先安裝 GitHub Copilot CLI：\n\n"
            "1. 用 winget（建議，PowerShell 直接執行）：\n"
            "     winget install GitHub.Copilot\n\n"
            "2. 或用 npm：\n"
            "     npm install -g @github/copilot\n\n"
            "3. 或到 Microsoft Store 搜尋「GitHub Copilot CLI」安裝。\n\n"
            "裝好後確認 copilot.exe 在 PATH 中\n"
            "（預設位置：%LOCALAPPDATA%\\GitHubCopilotCLI\\copilot.exe），\n"
            "重新啟動桌寵即可。\n\n"
            "也可以直接把路徑寫進 settings.json：\n"
            '  {"watcher": {"copilot_path": "C:\\\\path\\\\to\\\\copilot.exe"}}'
        )
    if IS_MACOS:
        return (
            "請先安裝 GitHub Copilot CLI：\n\n"
            "  brew install github/copilot/copilot\n"
            "  或 npm install -g @github/copilot\n\n"
            "安裝完後重新啟動桌寵。"
        )
    return (
        "請先安裝 GitHub Copilot CLI：\n\n"
        "1. 官方安裝腳本：\n"
        "     curl -fsSL https://raw.githubusercontent.com/github/copilot-cli/main/scripts/install.sh | bash\n\n"
        "2. 或取得 copilot binary 並放到 PATH：\n"
        "     mv copilot ~/.local/bin/copilot\n"
        "     chmod +x ~/.local/bin/copilot\n\n"
        "安裝完後重新啟動桌寵。"
    )


# ══════════════════════════════════════════════════════════
# 開機自動啟動（Windows 用 HKCU Run 機碼；Linux 寫 .desktop）
# ══════════════════════════════════════════════════════════

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ZhenXunDesktopPet"


def launch_command():
    """重啟自己用的指令（盡量用 pythonw 避免黑視窗）。"""
    exe = sys.executable
    if IS_WINDOWS:
        pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pythonw):
            exe = pythonw
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        return f'"{exe}" "{script}"'
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{exe}" "{script}"'


def autostart_enabled() -> bool:
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(value)
        except OSError:
            return False
        except Exception:
            return False
    # Linux：XDG autostart
    desktop = os.path.join(os.path.expanduser("~"), ".config", "autostart",
                           "zhenxun-desktop-pet.desktop")
    return os.path.exists(desktop)


def autostart_set(enable: bool):
    """回傳 (成功與否, 訊息)。"""
    if IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                 winreg.KEY_SET_VALUE)
            with key:
                if enable:
                    winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
                    return True, "已設定開機自動啟動"
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except OSError:
                    pass
            return True, ("已設定開機自動啟動" if enable else "已取消開機自動啟動")
        except Exception as exc:
            return False, f"設定失敗：{exc}"

    desktop = os.path.join(os.path.expanduser("~"), ".config", "autostart",
                           "zhenxun-desktop-pet.desktop")
    try:
        if enable:
            os.makedirs(os.path.dirname(desktop), exist_ok=True)
            with open(desktop, "w", encoding="utf-8") as fh:
                fh.write("[Desktop Entry]\nType=Application\n"
                         f"Name={APP_NAME}\nExec={launch_command()}\n"
                         "X-GNOME-Autostart-enabled=true\n")
            return True, "已設定開機自動啟動"
        if os.path.exists(desktop):
            os.remove(desktop)
        return True, "已取消開機自動啟動"
    except Exception as exc:
        return False, f"設定失敗：{exc}"


# ══════════════════════════════════════════════════════════
# 螢幕幾何（多螢幕 / DPI 縮放安全）
# ══════════════════════════════════════════════════════════

def screen_at(point=None):
    """取得某個座标所在的螢幕（拿不到就退回主螢幕）。"""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return None
    if point is not None:
        scr = QApplication.screenAt(point)
        if scr is not None:
            return scr
    return QApplication.primaryScreen()


def available_geometry(point=None):
    """
    可用工作區（已排除工作列 / 面板）。
    原版用 geometry() 減掉固定 60px，在 Windows 上會被工作列挡到一半。
    """
    scr = screen_at(point)
    if scr is None:
        from PyQt6.QtCore import QRect
        return QRect(0, 0, 1920, 1080)
    return scr.availableGeometry()


def union_geometry(available: bool = False):
    """所有螢幕合成的範圍，用來判斷視窗还有沒有「在桌面上」。"""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QRect
    app = QApplication.instance()
    if app is None:
        return available_geometry()
    rect = QRect()
    for scr in QApplication.screens():
        g = scr.availableGeometry() if available else scr.geometry()
        rect = rect.united(g) if rect.isValid() else QRect(g)
    return rect if rect.isValid() else available_geometry()


def clamp_into_desktop(pos, size, margin: int = 0):
    """
    把視窗位置限制在桌面範圍內，避免被拖到螢幕外就再也抓不回來
    （Windows 上這是桌寵最常見的「消失」問題）。
    """
    from PyQt6.QtCore import QPoint, QSize
    avail = available_geometry(QPoint(pos.x() + size.width() // 2,
                                      pos.y() + size.height() // 2))
    full = union_geometry(available=True)

    # 完全在桌面外（例如拔掉了副螢幕）→ 直接拉回主螢幕右下角
    win_rect = _rect(pos, size)
    if not full.isValid() or not full.intersects(win_rect):
        return QPoint(avail.right() - size.width() - margin,
                      avail.bottom() - size.height() - margin)

    x = max(avail.left() + margin,
            min(pos.x(), avail.right() - size.width() - margin))
    y = max(avail.top() + margin,
            min(pos.y(), avail.bottom() - size.height() - margin))
    return QPoint(x, y)


def _rect(pos, size):
    from PyQt6.QtCore import QRect
    return QRect(pos.x(), pos.y(), size.width(), size.height())
