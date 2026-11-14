"""
config.py - 設定檔載入（跨平台）

桌寵的可調參數原本散在 pet_window.py / animation.py 裡，
改成可以在同資料夾放一個 settings.json 覆寫，
這樣 Windows 使用者（或不同螢幕縮放）不用改程式碼就能調。

settings.json 範例（只要寫想改的欄位即可）：

{
  "pet_size": [200, 200],
  "auto_trim": true,
  "animation_fps": { "idle": 6, "working": 12 },
  "watcher": { "poll_interval": 1.0 }
}
"""
import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULTS = {
    # 未裁剪時的顯示畫布尺寸（像素）
    "pet_size": [240, 240],
    # 依圖片實際內容裁掉多餘透明邊：人物的視覺大小不變，但視窗會貼著人物，
    # 比較不會挡到桌面後面的圖示。
    # 目前這套素材本來就填滿畫布（1024 中佔了 956），裁剪只省下十來像素，
    # 因此預設關閉以維持與原版完全一致的呈現；
    # 如果你的素材四周留白很多，把它設成 true 效果比較明顯。
    "auto_trim": False,
    "trim_alpha_threshold": 8,
    # 各動畫 FPS 覆寫，未列出的用 animation.ANIM_CONFIG 預設值
    "animation_fps": {},
    "bubble_font_px": 13,
    # 互動判定（毫秒）
    "poke_max_ms": 250,
    "pat_max_ms": 800,
    "grab_ms": 800,
    # 按住後要移動幾像素才算「拖曳」。沒超過門檻的按住放開才會判定
    # 點一下／摸頭 —— 避免拖完桌寵顺手又戳牠一下（原版沒有門檻）。
    "drag_threshold_px": 5,
    "confirm_auto_hide_ms": 10000,
    # Copilot 程序監聽
    "watcher": {
        # null = 自動（Windows / Linux 都是 0.5 秒；
        #        Windows 改用 Toolhelp32 快照，一次掃描約 17ms，很輕）
        "poll_interval": None,
        # 額外當成「Copilot 正在工作」的程序名，例如 ["node.exe"]
        "extra_process_names": [],
        # 只比程序名（不解析完整路徑）：在 Windows 上較快，但可能誤判
        "match_by_name_only": False,
    },
    # 通知
    "notify_sound": True,          # Windows 完成時播放系統提示音
    "start_hidden": False,         # 啟動時不顯示桌寵（只在系統匣）
    "click_through": False,        # 滑鼠穿透（視窗不挡點擊）
}

_loaded = None


def _merge(base: dict, extra: dict) -> dict:
    """把 settings.json 的內容併進預設值（巢狀 dict 也合併）。"""
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    global _loaded
    if _loaded is not None:
        return _loaded
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:                      # 設定檔壞掉不影響啟動
            import sys
            print(f"[config] settings.json 載入失敗，改用預設值：{exc}", file=sys.stderr)
            data = {}
    _loaded = _merge(DEFAULTS, data)
    return _loaded


def get(key, default=None):
    return load().get(key, default)


def fps_for(state: str, fallback: int) -> int:
    try:
        return int(load()["animation_fps"].get(state, fallback))
    except Exception:
        return fallback


def watcher(key, default=None):
    return load()["watcher"].get(key, default)
