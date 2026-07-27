"""菜单栏/托盘 + 全局热键。命令经队列交给主循环执行。"""

from __future__ import annotations

import sys
import threading
from typing import Any, Callable


IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")


class WinFocusKeys:
    """
    Windows：不依赖 pygame 窗口焦点的快捷键桥。
    仅在「鼠标在小猫上 / 刚点过小猫」时生效，避免抢其它窗口的输入。
    """

    def __init__(self, enqueue: Callable[[str], None], is_active: Callable[[], bool]):
        self._enqueue = enqueue
        self._is_active = is_active
        self._listener = None
        self._alt = False
        self._ctrl = False
        self._shift = False
        self._win = False

    def start(self) -> None:
        try:
            from pynput import keyboard
        except ImportError:
            print("⚠️ 未安装 pynput，Windows 焦点外快捷键不可用")
            return

        def on_press(key):
            self._update_mods(key, True)
            if not self._is_active():
                return
            # Ctrl+Alt 留给全局热键；Ctrl/Win 单独修饰时不抢单键
            if self._ctrl or self._win:
                return
            cmd = self._map_key(key)
            if cmd:
                self._enqueue(cmd)

        def on_release(key):
            self._update_mods(key, False)

        try:
            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.daemon = True
            self._listener.start()
            print("Windows 快捷键: 鼠标放在小猫上（或刚点过）即可按 N/C/Alt+M…，无需点进控制台")
        except Exception as exc:
            print(f"⚠️ Windows 快捷键桥启动失败: {exc}")
            self._listener = None

    def stop(self) -> None:
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass
        self._listener = None

    def _update_mods(self, key, pressed: bool) -> None:
        try:
            from pynput.keyboard import Key
        except ImportError:
            return
        if key in (Key.alt, Key.alt_l, Key.alt_r):
            self._alt = pressed
        elif key in (Key.ctrl, Key.ctrl_l, Key.ctrl_r):
            self._ctrl = pressed
        elif key in (Key.shift, Key.shift_l, Key.shift_r):
            self._shift = pressed
        elif key in (Key.cmd, Key.cmd_l, Key.cmd_r):
            self._win = pressed

    def _map_key(self, key) -> str | None:
        try:
            from pynput.keyboard import Key
        except ImportError:
            return None
        if key == Key.esc:
            return "keyesc"
        if key == Key.space:
            return "keyspace"
        if key == Key.left:
            return "keyleft"
        if key == Key.right:
            return "keyright"
        if key == Key.up:
            return "keyup"
        if key == Key.down:
            return "keydown"

        ch = None
        try:
            if getattr(key, "char", None):
                ch = str(key.char)
        except Exception:
            ch = None
        if not ch:
            vk = getattr(key, "vk", None)
            if isinstance(vk, int):
                # 字母 / 数字（Alt 按下时 char 常为空）
                if 65 <= vk <= 90:
                    ch = chr(vk)
                elif 48 <= vk <= 57:
                    ch = chr(vk)
                elif 96 <= vk <= 105:  # numpad
                    ch = chr(ord("0") + (vk - 96))
                else:
                    punct = {
                        186: ";",
                        188: ",",
                        189: "-",
                        190: ".",
                        191: "/",
                        187: "=",
                        219: "[",
                        220: "\\",
                        221: "]",
                        222: "'",
                    }
                    ch = punct.get(vk)
        if not ch:
            return None
        ch = ch.lower()[:1]
        if not ch:
            return None
        if self._alt:
            return f"keyalt:{ch}"
        return f"key:{ch}"


class DesktopChrome:
    """
    后台 UI：Mac NSStatusItem / Win 系统托盘；pynput 全局热键。
    所有动作只往 queue 丢字符串命令，由 pet.tick 里 drain。
    """

    def __init__(
        self,
        enqueue: Callable[[str], None],
        settings: dict[str, Any],
        win_keys_active: Callable[[], bool] | None = None,
    ):
        self._enqueue = enqueue
        self.settings = settings
        self._win_keys_active = win_keys_active
        self._hotkey_listener = None
        self._win_focus_keys: WinFocusKeys | None = None
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
            if self._win_keys_active is not None:
                self._win_focus_keys = WinFocusKeys(self._enqueue, self._win_keys_active)
                self._win_focus_keys.start()

    def stop(self) -> None:
        try:
            if self._hotkey_listener is not None:
                self._hotkey_listener.stop()
        except Exception:
            pass
        self._hotkey_listener = None
        if self._win_focus_keys is not None:
            try:
                self._win_focus_keys.stop()
            except Exception:
                pass
            self._win_focus_keys = None
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
            hk.get("story", "<ctrl>+<alt>+t"): lambda: self._emit("story"),
            hk.get("ai", "<ctrl>+<alt>+a"): lambda: self._emit("toggle_ai"),
        }
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys(mapping)
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
            print(
                "全局热键: Ctrl+Alt+R召唤 /总览 P穿透 S状态 B对喷 T故事 A开AI Q退出"
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
        add("开关会计猫", "toggle_buddy")
        add("故事大会", "story")
        add("开关休息提醒", "toggle_rest")
        add("开关鼠标寻访", "toggle_mouse_seek")
        menu.addItem_(NSMenuItem.separatorItem())
        add("随机猫互动", "cat_random")
        add("连喵", "cat_meow")
        add("晒太阳", "cat_sun")
        add("抓挠", "cat_scratch")
        add("叼礼物", "cat_gift")
        add("死盯", "cat_stare")
        add("推桌", "cat_knock")
        add("蹭头", "cat_headbutt")
        add("颤叫观鸟", "cat_chirp")
        add("傲娇无视", "cat_ignore")
        add("踩奶", "cat_knead")
        add("舔毛", "cat_groom")
        menu.addItem_(NSMenuItem.separatorItem())
        add("开关 AI", "toggle_ai")
        add("切换 AI 厂商", "cycle_ai_provider")
        add("显示称号", "titles")
        add("重载话术包", "reload_packs")
        menu.addItem_(NSMenuItem.separatorItem())
        add("退出", "quit")

        item.setMenu_(menu)
        self._status_item = item
        print("菜单栏: 点击 🐱 图标")

    def _start_win_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as exc:
            print(f"⚠️ 托盘需要: pip install pystray pillow ({exc})")
            return

        # 简易图标
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((8, 16, 56, 52), fill=(60, 40, 30, 255))
        draw.ellipse((20, 10, 32, 22), fill=(60, 40, 30, 255))
        draw.ellipse((32, 10, 44, 22), fill=(60, 40, 30, 255))

        def on(cmd: str):
            def _handler(_icon=None, _item=None):
                self._emit(cmd)
            return _handler

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
            pystray.MenuItem("开关会计猫", on("toggle_buddy")),
            pystray.MenuItem("故事大会", on("story")),
            pystray.MenuItem("开关休息提醒", on("toggle_rest")),
            pystray.MenuItem("开关鼠标寻访", on("toggle_mouse_seek")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("随机猫互动", on("cat_random")),
            pystray.MenuItem("连喵", on("cat_meow")),
            pystray.MenuItem("晒太阳", on("cat_sun")),
            pystray.MenuItem("抓挠", on("cat_scratch")),
            pystray.MenuItem("叼礼物", on("cat_gift")),
            pystray.MenuItem("死盯", on("cat_stare")),
            pystray.MenuItem("推桌", on("cat_knock")),
            pystray.MenuItem("蹭头", on("cat_headbutt")),
            pystray.MenuItem("颤叫观鸟", on("cat_chirp")),
            pystray.MenuItem("傲娇无视", on("cat_ignore")),
            pystray.MenuItem("踩奶", on("cat_knead")),
            pystray.MenuItem("舔毛", on("cat_groom")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开关 AI", on("toggle_ai")),
            pystray.MenuItem("切换 AI 厂商", on("cycle_ai_provider")),
            pystray.MenuItem("显示称号", on("titles")),
            pystray.MenuItem("重载话术包", on("reload_packs")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", on("quit")),
        )
        icon = pystray.Icon("cockroach_pet", img, "小猫桌宠", menu)
        self._tray_icon = icon
        print("系统托盘: 右键任务栏旁小猫图标（若在隐藏区点 ^ 展开）")
        try:
            icon.run()
        except Exception as exc:
            print(f"⚠️ 托盘运行失败: {exc}")
            self._tray_icon = None
