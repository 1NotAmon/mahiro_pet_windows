"""
copilot_watcher.py - 用獨立 thread 監聽 copilot process

Windows 版改動
--------------
1. 【重要】Windows 11 自帶 Microsoft Copilot（copilotapp.exe 常駐十幾個程序、
   copilot-runtime 等）。原版「名稱含有 copilot 就算在跑」的直覺規則
   會把桌寵永久判成「Copilot 工作中」，而且每秒對這些程序解析
   exe()/cmdline() 非常貴（實測把背景動畫載入從 3.3 秒拖到 10.9 秒）。
   → 改成「程序名完整比對 + 執行檔實路徑比對」，並把內建 Copilot App
     放進排除清單。
2. 原版只比 os.path.realpath(exe)。Windows 的路徑大小寫不同（實測本機是
   copilot.EXE）、可能有 8.3 短檔名、也可能用 npm 的 copilot.cmd 包裝，
   改成 normcase 比對；只有在偵測目標不是 .exe（即 npm 那類 shim）時，
   才會去檢查 node.exe 的 command line。
3. psutil.process_iter 一律先只取 name（便宜），名稱符合才解析昂貴的 exe。
4. STATUS_ZOMBIE 是 Linux-only 的概念，用 getattr 保護並在 Linux 才請求該屬性。
5. stop() 會 join 執行緒，並改用 Event.wait()，關閉時立刻醒來不會卡住。
"""
import os
import threading

import psutil
from PyQt6.QtCore import QObject, pyqtSignal

import config
from platform_utils import IS_LINUX, IS_WINDOWS, copilot_match_names, find_copilot


# Microsoft Copilot（系統內建 App / 各編輯器內建 Copilot），
# 不是我們要監聽的 GitHub Copilot CLI
EXCLUDE_NAMES = {
    "copilotapp.exe", "copilotapphost.exe", "copilot-runtime.exe",
    "copilotbrowserhub.exe", "copilot.exe~", "microsoftcopilot.exe",
    "copilotnotifications.exe", "copilot.exe.mui",
}

# npm 型安裝：實際程序是 node.exe，指令裡才有 copilot
NODE_EXE_NAMES = {"node.exe", "node"}


# ── Windows 程序快照（比 psutil 快很多，只在命中時才用 psutil 查路徑）──
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]


def _win_process_snapshot():
    """回傳 [(pid, 小寫程序名)]；失敗抛例外，呼叫端退回 psutil。"""
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (None, INVALID_HANDLE_VALUE):
        raise OSError("CreateToolhelp32Snapshot 失敗")
    out = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            name = (entry.szExeFile or "").lower()
            out.append((int(entry.th32ProcessID), name))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    if not out:
        raise OSError("程序快照是空的")
    return out


def _find_copilot_path() -> str:
    """
    找出要監聽的 copilot 執行檔。
    優先順序：settings.json 指定 > PATH > 各平台常見安裝位置 > "copilot"
    """
    configured = config.watcher("copilot_path")
    if configured:
        expanded = os.path.expanduser(configured)
        if os.path.exists(expanded):
            return os.path.realpath(expanded)
    found = find_copilot()
    if found:
        try:
            return os.path.realpath(found)
        except Exception:
            return found
    return "copilot"


COPILOT_BIN = _find_copilot_path()
# 只比「完整程序名」，不用「包含 copilot」；大小寫正規化
COPILOT_NAMES = {n.lower() for n in copilot_match_names(COPILOT_BIN)} - EXCLUDE_NAMES
COPILOT_BIN_NORM = os.path.normcase(COPILOT_BIN)
# 偵測目標不是可執行檔（例如 npm 的 .cmd/.js 包裝）才需要查 cmdline
NODE_HOSTED = COPILOT_BIN.lower().endswith((".cmd", ".bat", ".js", ".ps1", ".mjs"))


class CopilotWatcher(QObject):
    copilot_started = pyqtSignal()
    copilot_stopped = pyqtSignal()
    status_changed  = pyqtSignal(str)

    # 改用 Toolhelp32 快照後，Windows 一次掃描只要約 17ms（原本用
    # psutil + status 屬性要 3200ms），所以兩邊都可以用 0.5 秒，
    # 偵測「剛開始 / 剛結束」的反應更快。可在 settings.json 調。
    POLL_INTERVAL = 0.5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active  = False
        self._running = False
        self._thread  = None
        self._wake    = threading.Event()
        # 只有 Linux 有殭屍程序。注意：psutil 在 Windows 也定義了
        # STATUS_ZOMBIE 這個「常數」，所以不能用 getattr 有沒有值來判斷，
        # 否則會多請求 status 屬性 —— 實測 psutil 的 Windows status()
        # 會去查 proc_is_suspended，350 個程序要花 2.3 秒，掃描直接被拖垮。
        self._zombie = psutil.STATUS_ZOMBIE if IS_LINUX else None

        interval = config.watcher("poll_interval")
        if interval:
            try:
                self.POLL_INTERVAL = max(0.2, float(interval))
            except (TypeError, ValueError):
                pass
        self.match_by_name_only = bool(config.watcher("match_by_name_only", False))
        self.extra_names = {str(n).lower()
                            for n in (config.watcher("extra_process_names", []) or [])}

    # ── 生命週期 ────────────────────────────────────────────
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._active = True
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="copilot-watcher")
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        self._active = False
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout)
            except RuntimeError:
                pass      # 從回呼裡叫 stop() 時不能 join 自己
        self._thread = None

    # ── 主迴圈 ──────────────────────────────────────────────
    def _loop(self):
        prev = False
        while self._active:
            try:
                found = self._detect_copilot()
            except Exception:
                found = False
            if self._active and found != prev:
                self._running = found
                if found:
                    self.copilot_started.emit()
                    self.status_changed.emit("Copilot 工作中...")
                elif prev:
                    self.copilot_stopped.emit()
                    self.status_changed.emit("待機中")
            prev = found          # ★ 少了這行會每次輪詢都重發 started（踩過的坑）
            self._wake.wait(self.POLL_INTERVAL)
            self._wake.clear()

    # ── 偵測 ────────────────────────────────────────────────
    def _detect_copilot(self) -> bool:
        if IS_WINDOWS:
            found = self._detect_copilot_windows()
            if found is not None:
                return found
            # 快照失敗就退回 psutil 通用路徑
        attrs = ["pid", "name"] + (["status"] if self._zombie else [])
        for proc in psutil.process_iter(attrs):
            if self._consider(proc, (proc.info.get("name") or "").lower()):
                return True
        return False

    def _detect_copilot_windows(self):
        """
        Windows 快速路徑：用 Toolhelp32 快照拿 (pid, 程序名)，
        名稱完全不符就直接結束這一輪 —— 實測 350 個程序只要約 3ms，
        而 psutil.process_iter(['name']) 要 12ms、再加上 status 要 2300ms。
        只有名稱命中才會用 psutil 去解析完整 exe 路徑。
        回傳 None 代表快照不可用（呼叫端改用 psutil 通用路徑）。
        """
        try:
            snapshot = _win_process_snapshot()
        except Exception:
            return None
        watch = COPILOT_NAMES | self.extra_names
        if NODE_HOSTED:
            watch = watch | NODE_EXE_NAMES
        for pid, name in snapshot:
            if not name or name in EXCLUDE_NAMES:
                continue
            if name not in watch:
                continue
            if name in self.extra_names or self.match_by_name_only:
                return True
            try:
                proc = psutil.Process(pid)
            except Exception:
                continue
            if self._exe_matches(proc, name):
                return True
        return False

    def _consider(self, proc, name: str) -> bool:
        """psutil 通用路徑（Linux / macOS；Windows 快照失敗時的備援）。"""
        try:
            if not name or name in EXCLUDE_NAMES:
                return False
            if name in self.extra_names:
                return True
            # 便宜的比對放前面：名稱完全相符才去做昂貴的 exe 解析
            if name not in COPILOT_NAMES and not (
                    NODE_HOSTED and name in NODE_EXE_NAMES):
                return False
            if self._zombie and proc.info.get("status") == self._zombie:
                return False
            if self.match_by_name_only:
                return True
            return self._exe_matches(proc, name)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False
        except Exception:
            return False

    def _exe_matches(self, proc, name: str) -> bool:
        try:
            exe = proc.exe()
        except Exception:
            exe = None

        if exe:
            try:
                if os.path.normcase(os.path.realpath(exe)) == COPILOT_BIN_NORM:
                    return True
            except Exception:
                pass

        if NODE_HOSTED and name in NODE_EXE_NAMES:
            # npm 的 copilot.cmd 其實是 node 跑 script，只能看 command line
            try:
                cmdline = " ".join(proc.cmdline()).lower()
            except Exception:
                cmdline = ""
            if "copilot" in cmdline and "cli" in cmdline:
                return True
            if os.path.basename(COPILOT_BIN_NORM) in cmdline:
                return True

        if not exe:
            # 權限不足拿不到 exe 時，退回程序名判斷（EXCLUDE 已先擋掉內建 Copilot）
            return name in COPILOT_NAMES
        return False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def target(self) -> str:
        return COPILOT_BIN

    @property
    def watched_names(self):
        return sorted(COPILOT_NAMES)
