"""系统专注模式 + 会议/共享/投屏/截图检测，供桌宠自动静默。"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Iterable

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    psutil = None  # type: ignore
    PSUTIL_OK = False

# 专用会议客户端：进程在跑 → meeting；共享宿主/标题 → sharing
# 飞书/钉钉/企微等常驻 IM 不因进程在跑就判定开会，只靠窗口标题 / 共享信号
MEETING_PROCESS_NAMES = frozenset(
    {
        "zoom.exe",
        "zoom",
        "zoom.us",
        "cpthost.exe",
        "cpthost",
        "teams.exe",
        "teams",
        "ms-teams.exe",
        "ms-teams",
        "sharinghost.exe",
        "sharinghost",
        "wemeetapp.exe",
        "wemeetapp",
        "wemeetapp_new.exe",
        "tencentmeeting.exe",
        "tencentmeeting",
        "wemeet_remote_control.exe",
        "feishumeeting.exe",
        "ciscocollabhost.exe",
        "ciscowebexstart.exe",
        "webex.exe",
        "webex",
        "todesk.exe",
        "todesk",
        "sunloginclient.exe",
        "orayremote.exe",
    }
)

SHARE_PROCESS_NAMES = frozenset(
    {
        "cpthost.exe",
        "cpthost",
        "sharinghost.exe",
        "sharinghost",
        "wemeet_remote_control.exe",
    }
)

# 投屏/镜像相关进程（不含常驻、易误判的 AirPlayUIAgent）
CAST_PROCESS_NAMES = frozenset(
    {
        # Windows 无线投影 / Connect
        "screencastingbar.exe",
        "screencastingbar",
        "miracastview.exe",
        "miracastview",
        # 常见投屏工具（活动会话时常有独立进程）
        "apowermirror.exe",
        "apowermirror",
        "lebocast.exe",
        "lebocast",
        "lelink.exe",
        "lelink",
        "seewolink.exe",
        "seewolink",
        "scrcpy.exe",
        "scrcpy",
        "wscreen.exe",
        "wscreen",
        "letsview.exe",
        "letsview",
        "airserver.exe",
        "airserver",
        "reflector.exe",
        "reflector",
        "spacedesk-server.exe",
        "spacedesk-server",
        "deskreen",
        "deskreen.exe",
    }
)

# 截图时短暂出现的系统/工具进程（不含 Snipaste/ShareX 等常驻托盘）
SCREENSHOT_PROCESS_NAMES = frozenset(
    {
        "screencapture",
        "screenshot",  # macOS Screenshot.app（⌘⇧5）
        "screenclippinghost.exe",
        "screenclippinghost",
        "screensketch.exe",
        "screensketch",
    }
)

SHARE_TITLE_KEYWORDS = (
    "正在共享",
    "共享屏幕",
    "你正在共享",
    "停止共享",
    "screen sharing",
    "you are sharing",
    "sharing your screen",
    "stop share",
    "stop sharing",
    "presenting",
    "you are presenting",
    "正在演示",
    "演示中",
)

MEETING_TITLE_KEYWORDS = (
    "视频会议",
    "正在通话",
    "会议中",
    "in a call",
    "in meeting",
    "microsoft teams meeting",
)

CAST_TITLE_KEYWORDS = (
    "正在投射",
    "投射到",
    "正在投影",
    "投影到",
    "投影到此电脑",
    "无线显示",
    "无线显示器",
    "投屏中",
    "正在投屏",
    "屏幕镜像",
    "镜像显示",
    "airplay",
    "screen mirroring",
    "mirroring",
    "projecting to",
    "projecting",
    "miracast",
    "duplicate these displays",
    "复制这些显示器",
    "第二屏幕",
    "connect to a wireless display",
)

SCREENSHOT_TITLE_KEYWORDS = (
    "截图",
    "屏幕截图",
    "screenshot",
    "screen shot",
    "snipping",
    "screen clipping",
    "截取",
)

_FOCUS_CACHE: tuple[float, bool | None] = (0.0, None)
_PRESENCE_CACHE: tuple[float, dict] = (0.0, {})


def mac_focus_active() -> bool | None:
    """尽力探测 macOS 专注/勿扰。失败返回 None。"""
    if not IS_MAC:
        return None
    try:
        home = os.path.expanduser("~")
        assertions = os.path.join(home, "Library/DoNotDisturb/DB/Assertions.json")
        if os.path.isfile(assertions):
            with open(assertions, encoding="utf-8") as f:
                data = json.load(f)
            store = data.get("data") or data.get("assertions") or []
            if isinstance(store, list):
                return len(store) > 0
            if isinstance(store, dict) and store:
                inner = store.get("assertions") or store.get("data") or []
                return bool(inner) if isinstance(inner, list) else True
            return False
        mode_cfg = os.path.join(home, "Library/DoNotDisturb/DB/ModeConfigurations.json")
        if os.path.isfile(mode_cfg):
            return False
    except Exception:
        return None
    return None


def win_focus_assist_active() -> bool | None:
    """尽力探测 Windows 专注助手 / 勿扰。失败返回 None。"""
    if not IS_WIN:
        return None
    try:
        import winreg
    except ImportError:
        return None

    candidates = [
        r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current\default$windows.data.notifications.quiethourssettings\windows.data.notifications.quiethourssettings",
        r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\$quiet\$windows.data.notifications.quiethourssettings\Current",
        r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current\default$windows.data.notifications.quiethourscorevalues\windows.data.notifications.quiethourscorevalues",
    ]
    for path in candidates:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
            try:
                data, typ = winreg.QueryValueEx(key, "Data")
            finally:
                winreg.CloseKey(key)
            if typ not in (winreg.REG_BINARY, 3) or not isinstance(data, (bytes, bytearray)):
                continue
            blob = bytes(data)
            if len(blob) < 8:
                continue
            for offset in (16, 18, 20, 24):
                if len(blob) > offset and blob[offset] in (1, 2):
                    return True
            if len(blob) > 24 and all(
                blob[o] == 0 for o in (16, 18, 20, 24) if len(blob) > o
            ):
                return False
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return None


def system_focus_active() -> bool | None:
    """跨平台专注/勿扰。True=开，False=关，None=未知。带 30s 缓存。"""
    global _FOCUS_CACHE
    now = time.time()
    ts, cached = _FOCUS_CACHE
    if now - ts < 30.0:
        return cached
    if IS_MAC:
        result = mac_focus_active()
    elif IS_WIN:
        result = win_focus_assist_active()
    else:
        result = None
    _FOCUS_CACHE = (now, result)
    return result


def _running_process_names() -> set[str]:
    if not PSUTIL_OK:
        return set()
    names: set[str] = set()
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                raw = proc.info.get("name") or ""
            except (psutil.Error, TypeError):
                continue
            if not raw:
                continue
            low = raw.lower()
            names.add(low)
            if low.endswith(".exe"):
                names.add(low[:-4])
            else:
                names.add(low + ".exe")
            if "zoom" in low:
                names.add("zoom")
                names.add("zoom.us")
    except Exception:
        return names
    return names


def _window_titles_win() -> list[str]:
    titles: list[str] = []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        IsWindowVisible = user32.IsWindowVisible
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                if not IsWindowVisible(hwnd):
                    return True
                length = GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buf, length + 1)
                t = (buf.value or "").strip()
                if t:
                    titles.append(t)
            except Exception:
                return True
            return True

        EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        return titles
    return titles


def _window_titles_mac() -> list[str]:
    titles: list[str] = []
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
            kCGWindowName,
        )

        arr = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
        for item in arr:
            try:
                name = item.get(kCGWindowName) or item.get("kCGWindowName") or ""
            except Exception:
                name = ""
            if name:
                titles.append(str(name))
    except Exception:
        return titles
    return titles


def _titles_match(titles: Iterable[str], keywords: Iterable[str]) -> bool:
    for t in titles:
        low = t.lower()
        for kw in keywords:
            if kw.lower() in low or kw in t:
                return True
    return False


def mac_display_mirroring() -> bool:
    """macOS：显示器处于镜像/AirPlay 镜像集合时返回 True。"""
    if not IS_MAC:
        return False
    # 优先 Quartz；沙箱/缺包时回退 ctypes
    try:
        from Quartz import CGDisplayIsInMirrorSet, CGGetActiveDisplayList

        err, active, count = CGGetActiveDisplayList(32, None, None)
        if not err and active and int(count) > 0:
            for did in list(active)[: int(count)]:
                try:
                    if CGDisplayIsInMirrorSet(int(did)):
                        return True
                except Exception:
                    continue
            return False
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("CoreGraphics")
        if not path:
            return False
        cg = ctypes.CDLL(path)
        get_list = cg.CGGetActiveDisplayList
        get_list.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_list.restype = ctypes.c_int32
        in_mirror = cg.CGDisplayIsInMirrorSet
        in_mirror.argtypes = [ctypes.c_uint32]
        in_mirror.restype = ctypes.c_uint32
        arr = (ctypes.c_uint32 * 32)()
        n = ctypes.c_uint32(0)
        if get_list(32, arr, ctypes.byref(n)) != 0:
            return False
        for i in range(int(n.value)):
            if in_mirror(arr[i]):
                return True
    except Exception:
        return False
    return False


def win_display_mirroring() -> bool:
    """
    Windows：检测「复制这些显示器」/克隆拓扑（含部分无线投影）。
    优先 QueryDisplayConfig（同源多 path）；失败则用「多适配器附着但仅 1 个监视器」启发。
    """
    if not IS_WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        QDC_ONLY_ACTIVE_PATHS = 0x00000002
        DISPLAYCONFIG_PATH_ACTIVE = 0x1

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

        class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
            _fields_ = [
                ("adapterId", LUID),
                ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT),
                ("statusFlags", wintypes.UINT),
            ]

        class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
            _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]

        class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
            _fields_ = [
                ("adapterId", LUID),
                ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT),
                ("outputTechnology", wintypes.UINT),
                ("rotation", wintypes.UINT),
                ("scaling", wintypes.UINT),
                ("refreshRate", DISPLAYCONFIG_RATIONAL),
                ("scanLineOrdering", wintypes.UINT),
                ("targetAvailable", wintypes.BOOL),
                ("statusFlags", wintypes.UINT),
            ]

        class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
            _fields_ = [
                ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
                ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
                ("flags", wintypes.UINT),
            ]

        # DISPLAYCONFIG_MODE_INFO ≈ 64 bytes（union）；仅作 Query 缓冲
        class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
            _fields_ = [
                ("infoType", wintypes.UINT),
                ("id", wintypes.UINT),
                ("adapterId", LUID),
                ("_mode", ctypes.c_byte * 48),
            ]

        path_count = wintypes.UINT(0)
        mode_count = wintypes.UINT(0)
        err = user32.GetDisplayConfigBufferSizes(
            QDC_ONLY_ACTIVE_PATHS,
            ctypes.byref(path_count),
            ctypes.byref(mode_count),
        )
        if err == 0 and path_count.value > 0:
            paths = (DISPLAYCONFIG_PATH_INFO * path_count.value)()
            modes = (DISPLAYCONFIG_MODE_INFO * max(1, mode_count.value))()
            pc = wintypes.UINT(path_count.value)
            mc = wintypes.UINT(max(1, mode_count.value))
            err = user32.QueryDisplayConfig(
                QDC_ONLY_ACTIVE_PATHS,
                ctypes.byref(pc),
                paths,
                ctypes.byref(mc),
                modes,
                None,
            )
            if err == 0:
                seen: dict[tuple, int] = {}
                for i in range(int(pc.value)):
                    p = paths[i]
                    if not (int(p.flags) & DISPLAYCONFIG_PATH_ACTIVE):
                        continue
                    key = (
                        int(p.sourceInfo.adapterId.LowPart),
                        int(p.sourceInfo.adapterId.HighPart),
                        int(p.sourceInfo.id),
                    )
                    seen[key] = seen.get(key, 0) + 1
                if any(n >= 2 for n in seen.values()):
                    return True
    except Exception:
        pass

    # 启发：多个物理显示附着到桌面，但 EnumDisplayMonitors 只报 1 个 → 多为「复制」
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
        DISPLAY_DEVICE_MIRRORING_DRIVER = 0x8
        DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4

        class DISPLAY_DEVICEW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128),
            ]

        attached = 0
        i = 0
        while True:
            dd = DISPLAY_DEVICEW()
            dd.cb = ctypes.sizeof(dd)
            if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
                break
            flags = int(dd.StateFlags)
            if flags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
                if not (flags & DISPLAY_DEVICE_MIRRORING_DRIVER):
                    attached += 1
            i += 1
            if i > 64:
                break

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        monitors: list[int] = []

        def _cb(hmon, hdc, lprect, lparam):
            monitors.append(1)
            return 1

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(RECT),
            wintypes.LPARAM,
        )
        user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_cb), 0)
        if attached >= 2 and len(monitors) == 1:
            return True
        _ = DISPLAY_DEVICE_PRIMARY_DEVICE  # 保留语义，避免未用告警
    except Exception:
        return False
    return False


def display_mirroring_active() -> bool:
    """跨平台：系统显示器镜像/复制中。"""
    if IS_MAC:
        return mac_display_mirroring()
    if IS_WIN:
        return win_display_mirroring()
    return False


def _empty_presence() -> dict:
    return {
        "meeting_level": "",
        "casting": False,
        "screenshot": False,
        "hide": False,
    }


def detect_presence(force: bool = False) -> dict:
    """
    综合检测。
    meeting_level: "" | "meeting" | "sharing"
    casting / screenshot: bool
    hide: 需要收起窗口（sharing | casting | screenshot）
    缓存约 2 秒。
    """
    global _PRESENCE_CACHE
    now = time.time()
    ts, cached = _PRESENCE_CACHE
    if not force and cached and now - ts < 2.0:
        return dict(cached)

    info = _empty_presence()
    names = _running_process_names()

    if names & SHARE_PROCESS_NAMES:
        info["meeting_level"] = "sharing"
    elif names & MEETING_PROCESS_NAMES:
        info["meeting_level"] = "meeting"

    if names & CAST_PROCESS_NAMES:
        info["casting"] = True
    if names & SCREENSHOT_PROCESS_NAMES:
        info["screenshot"] = True

    titles: list[str] = []
    if IS_WIN:
        titles = _window_titles_win()
    elif IS_MAC:
        titles = _window_titles_mac()

    if _titles_match(titles, SHARE_TITLE_KEYWORDS):
        info["meeting_level"] = "sharing"
    elif info["meeting_level"] != "sharing" and _titles_match(titles, MEETING_TITLE_KEYWORDS):
        info["meeting_level"] = "meeting"

    if _titles_match(titles, CAST_TITLE_KEYWORDS):
        info["casting"] = True
    if _titles_match(titles, SCREENSHOT_TITLE_KEYWORDS):
        # 标题含「截图」的常驻应用易误判；仅当同时有短暂截图进程，或系统截图 UI
        if info["screenshot"] or _titles_match(
            titles,
            ("screen clipping", "snipping", "屏幕截图", "screenshot"),
        ):
            info["screenshot"] = True

    if display_mirroring_active():
        info["casting"] = True

    info["hide"] = bool(
        info["meeting_level"] == "sharing"
        or info["casting"]
        or info["screenshot"]
    )
    _PRESENCE_CACHE = (now, dict(info))
    return info


def detect_meeting_presence(force: bool = False) -> str:
    """兼容旧接口：返回 "" | meeting | sharing。"""
    info = detect_presence(force=force)
    return str(info.get("meeting_level") or "")


def period_of_day_hour(hour: int | None = None) -> str:
    h = datetime.now().hour if hour is None else hour
    if h >= 23 or h < 5:
        return "night"
    if 18 <= h < 23:
        return "evening"
    if 12 <= h < 14:
        return "noon"
    return "day"


def routine_mode(respect_focus: bool = True, meeting_level: str = "") -> str:
    """
    作息模式: active | quiet | sleepish。
    meeting/sharing → quiet；专注开启 → quiet。
    """
    if meeting_level in ("meeting", "sharing"):
        return "quiet"
    if respect_focus:
        focus = system_focus_active()
        if focus is True:
            return "quiet"
    period = period_of_day_hour()
    if period == "night":
        return "sleepish"
    if period == "evening":
        return "quiet"
    return "active"
