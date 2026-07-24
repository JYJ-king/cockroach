"""菜单栏/托盘 + 全局热键。命令经队列交给主循环执行。"""

from __future__ import annotations

import queue
import sys
import threading
from typing import Any, Callable


IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")


class DesktopChrome:
    """
    后台 UI：Mac NSStatusItem / Win 系统托盘；pynput 全局热键。
    所有动作只往 queue 丢字符串命令，由 pet.tick 里 drain。
    """

    def __init__(self, enqueue: Callable[[str], None], settings: dict[str, Any]):
        self._enqueue = enqueue
        self.settings = settings
        self._hotkey_listener = None
        self._status_item = None  # mac
        self._tray_icon = None  # win
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self.settings.get("global_hotkeys", True):
            self._start_hotkeys()
        if IS_MAC:
            self._start_mac_status()
        elif IS_WIN:
            threading.Thread(target=self._start_win_tray, daemon=True).start()

    def stop(self) -> None:
        try:
            if self._hotkey_listener is not None:
                self._hotkey_listener.stop()
        except Exception:
            pass
        self._hotkey_listener = None
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    def _emit(self, cmd: str) -> None:
        try:
            self._enqueue(cmd)
        except Exception:
            pass

    def _start_hotkeys(self) -> None:
        try:
            from pynput import keyboard
        except ImportError:
            print("⚠️ 未安装 pynput，全局热键不可用: pip install pynput")
            return

        hk = self.settings.get("hotkeys") or {}
        mapping = {
            hk.get("call", "<ctrl>+<alt>+r"): lambda: self._emit("call"),
            hk.get("overview", "<ctrl>+<alt>+/"): lambda: self._emit("overview"),
            hk.get("passthrough", "<ctrl>+<alt>+p"): lambda: self._emit("passthrough"),
            hk.get("quit", "<ctrl>+<alt>+q"): lambda: self._emit("quit"),
            hk.get("status", "<ctrl>+<alt>+s"): lambda: self._emit("status"),
            hk.get("banter", "<ctrl>+<alt>+b"): lambda: self._emit("banter"),
        }
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys(mapping)
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
            print(
                "全局热键: Ctrl+Alt+R召唤 /总览 P穿透 S状态 B对喷 Q退出"
                "（Mac 若无效请在「辅助功能」允许终端/Python）"
            )
        except Exception as exc:
            print(f"⚠️ 全局热键启动失败: {exc}")
            self._hotkey_listener = None

    def _start_mac_status(self) -> None:
        try:
            import objc  # noqa: F401 — 确保 PyObjC runtime
            from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength
            from Foundation import NSObject
        except ImportError:
            print("⚠️ AppKit 不可用，跳过菜单栏")
            return

        chrome = self

        class StatusTarget(NSObject):
            def statusAction_(self, sender):  # noqa: N802
                try:
                    cmd = sender.representedObject()
                except Exception:
                    return
                if cmd:
                    chrome._emit(str(cmd))

        self._mac_target = StatusTarget.alloc().init()
        bar = NSStatusBar.systemStatusBar()
        item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        item.setTitle_("🪳")
        item.setHighlightMode_(True)

        menu = NSMenu.alloc().init()

        def add(title: str, cmd: str) -> None:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            mi.setRepresentedObject_(cmd)
            mi.setTarget_(self._mac_target)
            mi.setAction_("statusAction:")
            menu.addItem_(mi)

        add("召唤过来", "call")
        add("系统总览", "overview")
        add("切换点击穿透", "passthrough")
        add("状态", "status")
        menu.addItem_(NSMenuItem.separatorItem())
        add("切换气泡", "toggle_bubbles")
        add("切换打工提醒", "toggle_worker")
        add("切换财务提醒", "toggle_finance")
        add("切换系统告警", "toggle_sys")
        menu.addItem_(NSMenuItem.separatorItem())
        add("下一个皮肤", "next_skin")
        add("会计对喷", "banter")
        add("开关会计蟑螂", "toggle_buddy")
        add("显示称号", "titles")
        add("重载话术包", "reload_packs")
        menu.addItem_(NSMenuItem.separatorItem())
        add("退出", "quit")

        item.setMenu_(menu)
        self._status_item = item
        print("菜单栏: 点击 🪳 图标")

    def _start_win_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            print("⚠️ 托盘需要: pip install pystray pillow")
            return

        # 简易图标
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((8, 16, 56, 52), fill=(60, 40, 30, 255))
        draw.ellipse((20, 10, 32, 22), fill=(60, 40, 30, 255))
        draw.ellipse((32, 10, 44, 22), fill=(60, 40, 30, 255))

        def on(cmd: str):
            return lambda _icon, _item: self._emit(cmd)

        menu = pystray.Menu(
            pystray.MenuItem("召唤过来", on("call")),
            pystray.MenuItem("系统总览", on("overview")),
            pystray.MenuItem("切换点击穿透", on("passthrough")),
            pystray.MenuItem("状态", on("status")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("切换气泡", on("toggle_bubbles")),
            pystray.MenuItem("切换打工提醒", on("toggle_worker")),
            pystray.MenuItem("切换财务提醒", on("toggle_finance")),
            pystray.MenuItem("切换系统告警", on("toggle_sys")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("下一个皮肤", on("next_skin")),
            pystray.MenuItem("会计对喷", on("banter")),
            pystray.MenuItem("开关会计蟑螂", on("toggle_buddy")),
            pystray.MenuItem("显示称号", on("titles")),
            pystray.MenuItem("重载话术包", on("reload_packs")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", on("quit")),
        )
        icon = pystray.Icon("cockroach_pet", img, "蟑螂桌宠", menu)
        self._tray_icon = icon
        print("系统托盘: 右键蟑螂图标")
        icon.run()
