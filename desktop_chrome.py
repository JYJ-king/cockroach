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


class ScreenshotHotkeys:
    """
    截图快捷键判定（不单独开 pynput Listener）。
    Mac 上同时跑两个 keyboard Listener 会直接 abort，须并入 CombinedHotkeys。
    Mac: ⌘⇧3 / ⌘⇧4 / ⌘⇧5
    Win: PrintScreen / Win+Shift+S
    """

    def __init__(self, enqueue: Callable[[str], None]):
        self._enqueue = enqueue
        self._listener = None
        self.enabled = True
        self._cmd = False
        self._shift = False
        self._win = False
        self._ctrl = False

    def start(self) -> None:
        """兼容旧调用：单独启动（仅当没有其它 Listener 时使用）。"""
        try:
            from pynput import keyboard
        except ImportError:
            return

        def on_press(key):
            self.on_key(key, True)

        def on_release(key):
            self.on_key(key, False)

        try:
            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:
            print(f"⚠️ 截图热键监听失败: {exc}")
            self._listener = None

    def stop(self) -> None:
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass
        self._listener = None

    def on_key(self, key, pressed: bool) -> None:
        self._update_mods(key, pressed)
        if not pressed or not self.enabled:
            return
        if self._is_shot_combo(key):
            try:
                self._enqueue("stealth_shot")
            except Exception:
                pass

    def _update_mods(self, key, pressed: bool) -> None:
        try:
            from pynput.keyboard import Key
        except ImportError:
            return
        if key in (Key.cmd, Key.cmd_l, Key.cmd_r):
            # macOS ⌘；Windows 上 cmd 常映射为 Win
            self._cmd = pressed
            if IS_WIN:
                self._win = pressed
        elif key in (Key.shift, Key.shift_l, Key.shift_r):
            self._shift = pressed
        elif key in (Key.ctrl, Key.ctrl_l, Key.ctrl_r):
            self._ctrl = pressed
        # Win 键在部分环境是 Key.cmd，已在上面处理

    def _is_shot_combo(self, key) -> bool:
        try:
            from pynput.keyboard import Key
        except ImportError:
            return False
        # PrintScreen（含 Win+PrtSc）
        try:
            if key == Key.print_screen:
                return True
        except Exception:
            pass

        vk = getattr(key, "vk", None)
        ch = None
        try:
            if getattr(key, "char", None):
                ch = str(key.char).lower()
        except Exception:
            ch = None

        if IS_MAC:
            # ⌘⇧3/4/5；Shift 按下时 char 可能是 # $ %
            mac_vk_shot = {0x14, 0x15, 0x17, 20, 21, 23}  # ANSI 3/4/5
            if isinstance(vk, int) and vk in mac_vk_shot and self._cmd and self._shift:
                return True
            if self._cmd and self._shift and ch in ("3", "4", "5", "#", "$", "%"):
                return True
            return False

        if IS_WIN:
            # Win+Shift+S（Win 在 pynput 里常是 Key.cmd）
            if self._win and self._shift:
                if ch == "s":
                    return True
                if isinstance(vk, int) and vk in (0x53, 83):
                    return True
            return False
        return False


class CombinedHotkeys:
    """
    单一 pynput Listener：全局热键和弦 + 截图侦测。
    macOS 上两个 Listener 并存会 SIGABRT（keycode_context / ObjC）。
    """

    def __init__(
        self,
        enqueue: Callable[[str], None],
        mapping: dict[str, Callable[[], None]] | None,
        shot: ScreenshotHotkeys | None,
    ):
        self._enqueue = enqueue
        self._mapping = mapping or {}
        self._shot = shot
        self._listener = None
        self._hotkeys: list = []

    def start(self) -> None:
        try:
            from pynput import keyboard
        except ImportError:
            print("⚠️ 未安装 pynput，全局热键不可用: pip install pynput")
            return

        hotkeys = []
        for combo, cb in self._mapping.items():
            try:
                hotkeys.append(keyboard.HotKey(keyboard.HotKey.parse(combo), cb))
            except Exception as exc:
                print(f"⚠️ 热键无效 {combo}: {exc}")
        self._hotkeys = hotkeys

        def on_press(key):
            try:
                canonical = self._listener.canonical(key) if self._listener else key
            except Exception:
                canonical = key
            for hk in self._hotkeys:
                try:
                    hk.press(canonical)
                except Exception:
                    pass
            if self._shot is not None:
                self._shot.on_key(key, True)

        def on_release(key):
            try:
                canonical = self._listener.canonical(key) if self._listener else key
            except Exception:
                canonical = key
            for hk in self._hotkeys:
                try:
                    hk.release(canonical)
                except Exception:
                    pass
            if self._shot is not None:
                self._shot.on_key(key, False)

        try:
            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:
            print(f"⚠️ 热键监听启动失败: {exc}")
            self._listener = None

    def stop(self) -> None:
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass
        self._listener = None
        self._hotkeys = []


# Mac 菜单栏 target：模块级只注册一次，避免与 pynput 并发时重复 processClassDict
_MacStatusTarget = None


def _mac_status_target_type():
    global _MacStatusTarget
    if _MacStatusTarget is not None:
        return _MacStatusTarget
    from Foundation import NSObject

    class MacStatusTarget(NSObject):
        def statusAction_(self, sender):  # noqa: N802
            try:
                chrome = getattr(self, "_chrome", None)
                cmd = sender.representedObject()
            except Exception:
                return
            if chrome is not None and cmd:
                chrome._emit(str(cmd))

    _MacStatusTarget = MacStatusTarget
    return _MacStatusTarget


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
        progress: dict[str, Any] | None = None,
    ):
        self._enqueue = enqueue
        self.settings = settings
        self.progress = progress if isinstance(progress, dict) else {}
        self._win_keys_active = win_keys_active
        self._hotkey_listener = None
        self._win_focus_keys: WinFocusKeys | None = None
        self._shot_hotkeys: ScreenshotHotkeys | None = None
        self._combined: CombinedHotkeys | None = None
        self._status_item = None  # mac
        self._mac_menu = None
        self._mac_autosave_name = "cockroach.pet.menubar"
        self._mac_status_recovered = False
        self._mac_status_warned = False
        self._mac_status_deferred = False
        self._tray_icon = None  # win
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        # 先建菜单栏，再开 pynput，降低 ObjC/监听器竞态
        # Mac：等 NSApplication 跑起来后再挂菜单栏（Tahoe 上过早创建易不显示）
        if IS_MAC:
            self._mac_status_deferred = True
        elif IS_WIN:
            threading.Thread(target=self._start_win_tray, daemon=True).start()
            if self._win_keys_active is not None:
                self._win_focus_keys = WinFocusKeys(self._enqueue, self._win_keys_active)
                self._win_focus_keys.start()
        # 全局热键 + 截图侦测共用一个 Listener（Mac 双 Listener 会 abort）
        self._start_combined_input()

    def stop(self) -> None:
        try:
            if self._combined is not None:
                self._combined.stop()
        except Exception:
            pass
        self._combined = None
        try:
            if self._hotkey_listener is not None:
                self._hotkey_listener.stop()
        except Exception:
            pass
        self._hotkey_listener = None
        if self._shot_hotkeys is not None:
            try:
                self._shot_hotkeys.stop()
            except Exception:
                pass
            self._shot_hotkeys = None
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

    def set_shot_watch(self, enabled: bool) -> None:
        """开关截图躲闪侦测（不新建第二个 Listener）。"""
        if enabled:
            if self._shot_hotkeys is None:
                self._shot_hotkeys = ScreenshotHotkeys(self._enqueue)
            self._shot_hotkeys.enabled = True
            if self._combined is None:
                self._start_combined_input()
            elif self._combined._shot is None:
                self._combined._shot = self._shot_hotkeys
        else:
            if self._shot_hotkeys is not None:
                self._shot_hotkeys.enabled = False

    def _emit(self, cmd: str) -> None:
        try:
            self._enqueue(cmd)
        except Exception:
            pass

    def win_focus_keys_alive(self) -> bool:
        """Windows pynput 焦点外快捷键桥是否可用。"""
        wk = self._win_focus_keys
        return wk is not None and getattr(wk, "_listener", None) is not None

    def _hotkey_mapping(self) -> dict[str, Callable[[], None]]:
        hk = self.settings.get("hotkeys") or {}
        return {
            hk.get("call", "<ctrl>+<alt>+r"): lambda: self._emit("call"),
            hk.get("overview", "<ctrl>+<alt>+/"): lambda: self._emit("overview"),
            hk.get("passthrough", "<ctrl>+<alt>+p"): lambda: self._emit("passthrough"),
            hk.get("quit", "<ctrl>+<alt>+q"): lambda: self._emit("quit"),
            hk.get("status", "<ctrl>+<alt>+s"): lambda: self._emit("status"),
            hk.get("banter", "<ctrl>+<alt>+b"): lambda: self._emit("banter"),
            hk.get("story", "<ctrl>+<alt>+t"): lambda: self._emit("story"),
            hk.get("ai", "<ctrl>+<alt>+a"): lambda: self._emit("toggle_ai"),
        }

    def _start_combined_input(self) -> None:
        want_hotkeys = bool(self.settings.get("global_hotkeys", True))
        want_shot = bool(self.settings.get("meeting_silence", True))
        if not want_hotkeys and not want_shot:
            return
        mapping = self._hotkey_mapping() if want_hotkeys else {}
        if want_shot:
            if self._shot_hotkeys is None:
                self._shot_hotkeys = ScreenshotHotkeys(self._enqueue)
            self._shot_hotkeys.enabled = True
        shot = self._shot_hotkeys if want_shot else None
        try:
            if self._combined is not None:
                self._combined.stop()
            self._combined = CombinedHotkeys(self._enqueue, mapping, shot)
            self._combined.start()
            if self._combined._listener is None:
                self._combined = None
                return
            bits = []
            if want_hotkeys:
                bits.append("全局热键 Ctrl+Alt+R/…")
            if want_shot:
                bits.append("截图躲闪")
            print(
                "输入监听: "
                + " + ".join(bits)
                + "（Mac 若无效请在「辅助功能」允许终端/Python）"
            )
        except Exception as exc:
            print(f"⚠️ 输入监听启动失败: {exc}")
            self._combined = None

    def _start_hotkeys(self) -> None:
        """兼容旧路径：并入 CombinedHotkeys。"""
        self._start_combined_input()

    def _simple(self) -> bool:
        return bool(self.settings.get("simple_mode", True))

    # 菜单条目：(标题, 命令) ；None 表示分隔线
    _PRIMARY_SIMPLE = (
        ("召唤过来", "call"),
        ("摸头", "pet"),
        ("投喂", "feed"),
        ("纸箱", "box"),
        ("睡觉", "sleep"),
        ("专注番茄钟", "toggle_focus_pomodoro"),
        ("状态", "status"),
        ("重新开始引导", "replay_onboarding"),
    )

    _MORE_INTERACT = (
        ("随机猫互动", "cat_random"),
        ("连喵", "cat_meow"),
        ("晒太阳", "cat_sun"),
        ("抓挠", "cat_scratch"),
        ("叼礼物", "cat_gift"),
        ("死盯", "cat_stare"),
        ("推桌", "cat_knock"),
        ("蹭头", "cat_headbutt"),
        ("颤叫观鸟", "cat_chirp"),
        ("傲娇无视", "cat_ignore"),
        ("踩奶", "cat_knead"),
        ("舔毛", "cat_groom"),
        None,
        ("会计对喷", "banter"),
        ("财务脏话", "finance_swear"),
        ("故事大会", "story"),
        ("系统总览", "overview"),
    )

    _MORE_SETTINGS = (
        ("切换点击穿透", "passthrough"),
        ("切换气泡", "toggle_bubbles"),
        ("切换打工提醒", "toggle_worker"),
        ("切换财务提醒", "toggle_finance"),
        ("开关财务脏话", "toggle_finance_swear"),
        ("切换系统告警", "toggle_sys"),
        None,
        ("下一个皮肤", "next_skin"),
        ("下一个形象", "next_appearance"),
        ("锁定/解锁形象", "toggle_appearance_lock"),
        ("开关会计猫", "toggle_buddy"),
        ("标记月结中(应援)", "toggle_buddy_support"),
        ("这次不是月结(取消应援)", "dismiss_buddy_support"),
        ("开关休息提醒", "toggle_rest"),
        ("开关养生提醒", "toggle_care"),
        ("切换养生节奏", "cycle_care_preset"),
        ("专注番茄钟", "toggle_focus_pomodoro"),
        ("开关鼠标寻访", "toggle_mouse_seek"),
        ("开关自主行为", "toggle_autonomy"),
        ("开关会议/投屏静默", "toggle_meeting_silence"),
        None,
        ("开关 AI", "toggle_ai"),
        ("切换 AI 厂商", "cycle_ai_provider"),
        ("设置 AI 密钥", "set_ai_key"),
        ("显示称号", "titles"),
        ("重载话术包", "reload_packs"),
        ("重新开始引导", "replay_onboarding"),
    )

    def support_feedback_count(self) -> int:
        stats = (self.progress or {}).get("support_fp_stats") or {}
        return int(stats.get("total") or 0)

    def _menu_label(self, title: str, cmd: str) -> str:
        """动态菜单文案（误判反馈次数、养成状态等）。"""
        if cmd == "dismiss_buddy_support":
            n = self.support_feedback_count()
            if n > 0:
                return f"这次不是月结(已记{n}次)"
            return "这次不是月结(取消应援)"
        if cmd == "status":
            try:
                h = int((self.progress or {}).get("hunger", 100))
                f = int((self.progress or {}).get("fatigue", 100))
            except (TypeError, ValueError):
                h, f = 100, 100
            return f"状态(饥饿{h} 疲劳{f})"
        return title

    def rebuild_menus(self) -> None:
        """极简/完整模式切换后刷新菜单栏或托盘。"""
        if IS_MAC and self._status_item is not None:
            self._apply_mac_menu()
        elif IS_WIN and self._tray_icon is not None:
            try:
                self._tray_icon.menu = self._build_win_menu()
                self._tray_icon.update_menu()
            except Exception as exc:
                print(f"⚠️ 刷新托盘菜单失败: {exc}")

    def mac_status_tick(self) -> None:
        """主循环内：延迟创建菜单栏 + 一次性健康检查。"""
        if not IS_MAC:
            return
        try:
            self._mac_status_tick_impl()
        except Exception as exc:
            print(f"⚠️ 菜单栏 tick 异常（桌宠仍可用）: {exc}")

    def _mac_status_tick_impl(self) -> None:
        if self._mac_status_deferred:
            self._mac_status_deferred = False
            self._start_mac_status()
            return
        if self._mac_status_warned or self._status_item is None:
            return
        # 给 Control Center 约 1.5s 完成布局后再诊断
        import time
        if not hasattr(self, "_mac_status_check_after"):
            self._mac_status_check_after = time.time() + 1.5
            return
        if time.time() < self._mac_status_check_after:
            return
        self._mac_status_warned = True
        health = self._mac_status_health()
        if health.get("ok"):
            return
        if not self._mac_status_recovered and health.get("offscreen"):
            self._mac_recover_status_item(quiet=True)
            health = self._mac_status_health()
            if health.get("ok"):
                print("菜单栏: 已重新挂载离屏图标")
                return
        self._print_mac_status_help(health)

    def _mac_status_health(self) -> dict:
        """AppKit 坐标原点在左下：菜单栏图标的 y 应接近屏幕顶部（大数值）。"""
        item = self._status_item
        if item is None:
            return {"ok": False, "reason": "no_item"}
        visible = True
        if hasattr(item, "isVisible"):
            vis = item.isVisible
            visible = bool(vis() if callable(vis) else vis)
        btn = item.button() if hasattr(item, "button") else None
        frame_y = None
        screen_h = None
        screen_nil = False
        offscreen = False
        if btn is not None:
            win = btn.window()
            if win is not None:
                frame = win.frame()
                frame_y = float(frame.origin.y)
                scr = win.screen()
                if scr is None:
                    screen_nil = True
                else:
                    screen_h = float(scr.frame().size.height)
                # Tahoe 常见：y<0 飞出顶外；或误放到屏幕下半（y 过小）
                if frame_y < -2:
                    offscreen = True
                elif screen_h is not None and frame_y < screen_h * 0.5:
                    offscreen = True
            elif hasattr(btn, "frame"):
                bf = btn.frame()
                if float(bf.size.width) < 4:
                    offscreen = True
        ok = visible and not offscreen and not screen_nil
        return {
            "ok": ok,
            "visible": visible,
            "offscreen": offscreen,
            "screen_nil": screen_nil,
            "frame_y": frame_y,
            "screen_h": screen_h,
        }

    def _print_mac_status_help(self, health: dict) -> None:
        print("⚠️ 菜单栏图标未显示（macOS 26 Tahoe 常见）")
        print("   1. 系统设置 → 菜单栏 → 找到 Terminal 或 Python → 设为「在菜单栏中显示」")
        print("   2. 若仍无图标：系统设置 → 控制中心 → 菜单栏项 → 同上打开")
        print("   3. 备用：Ctrl+Shift+点击小猫（或 ⌘+⌥+点击）→ 弹出同款菜单")
        fy = health.get("frame_y")
        sh = health.get("screen_h")
        if fy is not None:
            extra = f"frame.y={fy}"
            if sh is not None:
                extra += f" screen.h={sh}"
            print(f"   （诊断: visible={health.get('visible')} {extra}）")

    def _mac_recover_status_item(self, quiet: bool = False) -> None:
        if self._mac_status_recovered:
            return
        self._mac_status_recovered = True
        try:
            from AppKit import NSStatusBar
        except ImportError:
            return
        if self._status_item is not None:
            try:
                NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            except Exception:
                pass
        self._status_item = None
        self._mac_menu = None
        self._mac_autosave_name = "cockroach.pet.menubar.recovered"
        self._start_mac_status(quiet=quiet)

    def pop_mac_menu_at_event(self, event) -> bool:
        """Mac：在鼠标位置弹出与菜单栏相同的 NSMenu。"""
        if not IS_MAC or self._mac_menu is None:
            return False
        try:
            win = event.window()
            pt = event.locationInWindow()
            if win is not None and hasattr(win, "convertPointToScreen_"):
                screen_pt = win.convertPointToScreen_(pt)
            else:
                from AppKit import NSMakePoint
                screen_pt = NSMakePoint(float(pt.x), float(pt.y))
            self._mac_menu.popUpMenuPositioningItem_atLocation_inView_(
                None, screen_pt, None
            )
            return True
        except Exception as exc:
            print(f"⚠️ 弹出菜单失败: {exc}")
            return False

    def pop_context_menu(self, screen_x: int | None = None, screen_y: int | None = None, event=None) -> bool:
        """双端：在指针旁弹出与菜单栏/托盘同结构的菜单（Ctrl+Shift+点小猫）。"""
        if IS_MAC:
            if event is not None:
                return self.pop_mac_menu_at_event(event)
            if self._mac_menu is None:
                return False
            try:
                from AppKit import NSEvent, NSMakePoint

                if screen_x is None or screen_y is None:
                    loc = NSEvent.mouseLocation()
                    screen_x, screen_y = int(loc.x), int(loc.y)
                self._mac_menu.popUpMenuPositioningItem_atLocation_inView_(
                    None, NSMakePoint(float(screen_x), float(screen_y)), None
                )
                return True
            except Exception as exc:
                print(f"⚠️ 弹出菜单失败: {exc}")
                return False
        if IS_WIN:
            return self._pop_win_context_menu(screen_x, screen_y)
        return False

    def _pop_win_context_menu(self, screen_x: int | None, screen_y: int | None) -> bool:
        """Windows：用 tkinter 在指针处弹出与托盘同结构的菜单。"""
        try:
            import tkinter as tk
        except ImportError:
            print("⚠️ 缺少 tkinter，无法弹出菜单；请用任务栏托盘")
            return False
        try:
            if screen_x is None or screen_y is None:
                import ctypes
                from ctypes import wintypes

                class POINT(ctypes.Structure):
                    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

                pt = POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                screen_x, screen_y = int(pt.x), int(pt.y)

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            menu = tk.Menu(root, tearoff=0)
            chosen: list[str] = []

            def add_cmd(parent, title: str, cmd: str) -> None:
                # 先记下选项，菜单销毁后再 _emit，避免 destroy 打断回调
                parent.add_command(
                    label=self._menu_label(title, cmd),
                    command=lambda c=cmd: chosen.append(c),
                )

            def add_cascade(parent, title: str, rows: tuple) -> None:
                sub = tk.Menu(parent, tearoff=0)
                for row in rows:
                    if row is None:
                        sub.add_separator()
                    else:
                        add_cmd(sub, row[0], row[1])
                parent.add_cascade(label=title, menu=sub)

            if self._simple():
                for title, cmd in self._PRIMARY_SIMPLE:
                    add_cmd(menu, title, cmd)
                menu.add_separator()
                add_cascade(menu, "更多互动", self._MORE_INTERACT)
                add_cascade(menu, "更多设置", self._MORE_SETTINGS)
                menu.add_separator()
                add_cmd(menu, "关闭极简模式", "toggle_simple_mode")
                add_cmd(menu, "退出", "quit")
            else:
                add_cmd(menu, "召唤过来", "call")
                add_cmd(menu, "摸头", "pet")
                add_cmd(menu, "投喂", "feed")
                add_cmd(menu, "纸箱", "box")
                add_cmd(menu, "睡觉", "sleep")
                add_cmd(menu, "系统总览", "overview")
                add_cmd(menu, "状态", "status")
                menu.add_separator()
                for row in self._MORE_SETTINGS:
                    if row is None:
                        menu.add_separator()
                    else:
                        add_cmd(menu, row[0], row[1])
                menu.add_separator()
                for row in self._MORE_INTERACT:
                    if row is None:
                        menu.add_separator()
                    else:
                        add_cmd(menu, row[0], row[1])
                menu.add_separator()
                add_cmd(menu, "开启极简模式", "toggle_simple_mode")
                add_cmd(menu, "退出", "quit")

            try:
                root.update_idletasks()
                menu.tk_popup(int(screen_x), int(screen_y))
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass
                try:
                    root.destroy()
                except Exception:
                    pass
            for cmd in chosen:
                self._emit(cmd)
            return True
        except Exception as exc:
            print(f"⚠️ Win 弹出菜单失败: {exc}")
            return False

    def _mac_status_button_image(self):
        """菜单栏图标：多符号回退 + 字号，避免 Tahoe 上 symbol 缺失导致零宽度。"""
        try:
            from AppKit import NSImage, NSImageSymbolConfiguration
        except ImportError:
            return None
        cfg = None
        if hasattr(NSImageSymbolConfiguration, "configurationWithPointSize_weight_"):
            try:
                cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(14.0, 0.0)
            except Exception:
                cfg = None
        for name in ("cat.fill", "pawprint.fill", "hare.fill", "face.smiling"):
            img = None
            if cfg is not None and hasattr(
                NSImage, "imageWithSystemSymbolName_accessibilityDescription_withConfiguration_"
            ):
                img = NSImage.imageWithSystemSymbolName_accessibilityDescription_withConfiguration_(
                    name, "小猫桌宠", cfg
                )
            if img is None and hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
                img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                    name, "小猫桌宠"
                )
            if img is not None:
                img.setTemplate_(True)
                return img
        return None

    def _configure_mac_status_item(self, item) -> None:
        from AppKit import NSImageLeft

        btn = item.button()
        img = self._mac_status_button_image()
        if btn is not None:
            btn.setTitle_("猫")
            if img is not None:
                btn.setImage_(img)
            btn.setImagePosition_(NSImageLeft)
            btn.setToolTip_("小猫桌宠")
        else:
            item.setTitle_("猫")
            if hasattr(item, "setHighlightMode_"):
                item.setHighlightMode_(True)
        name = getattr(self, "_mac_autosave_name", "cockroach.pet.menubar")
        if hasattr(item, "setAutosaveName_"):
            item.setAutosaveName_(name)
        if hasattr(item, "setVisible_"):
            item.setVisible_(True)

    def _start_mac_status(self, quiet: bool = False) -> None:
        try:
            import objc  # noqa: F401 — 确保 PyObjC runtime
            from AppKit import NSStatusBar, NSVariableStatusItemLength
        except ImportError:
            print("⚠️ AppKit 不可用，跳过菜单栏")
            return

        try:
            Target = _mac_status_target_type()
            self._mac_target = Target.alloc().init()
            self._mac_target._chrome = self
            bar = NSStatusBar.systemStatusBar()
            item = bar.statusItemWithLength_(NSVariableStatusItemLength)
            self._configure_mac_status_item(item)
            self._status_item = item
            self._apply_mac_menu()
            if not quiet:
                print("菜单栏: 点右上角「猫」或猫形图标（可能被 << 收起）")
                print("   若无图标: 系统设置→菜单栏→打开 Terminal/Python；或 ⌘+⌥+点小猫")
        except Exception as exc:
            import traceback
            print(f"⚠️ 菜单栏创建失败（桌宠仍可用）: {exc}")
            traceback.print_exc()
            self._status_item = None
            self._mac_menu = None

    def _apply_mac_menu(self) -> None:
        from AppKit import NSMenu, NSMenuItem

        menu = NSMenu.alloc().init()

        def add_to(parent, title: str, cmd: str) -> None:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                self._menu_label(title, cmd), None, ""
            )
            mi.setRepresentedObject_(cmd)
            mi.setTarget_(self._mac_target)
            mi.setAction_("statusAction:")
            parent.addItem_(mi)

        def add_sep(parent) -> None:
            parent.addItem_(NSMenuItem.separatorItem())

        def add_submenu(parent, title: str, items: tuple) -> None:
            sub = NSMenu.alloc().init()
            for row in items:
                if row is None:
                    add_sep(sub)
                else:
                    add_to(sub, row[0], row[1])
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            mi.setSubmenu_(sub)
            parent.addItem_(mi)

        if self._simple():
            for title, cmd in self._PRIMARY_SIMPLE:
                add_to(menu, title, cmd)
            add_sep(menu)
            add_submenu(menu, "更多互动", self._MORE_INTERACT)
            add_submenu(menu, "更多设置", self._MORE_SETTINGS)
            add_sep(menu)
            add_to(menu, "关闭极简模式", "toggle_simple_mode")
            add_to(menu, "退出", "quit")
        else:
            add_to(menu, "召唤过来", "call")
            add_to(menu, "摸头", "pet")
            add_to(menu, "投喂", "feed")
            add_to(menu, "纸箱", "box")
            add_to(menu, "睡觉", "sleep")
            add_to(menu, "系统总览", "overview")
            add_to(menu, "状态", "status")
            add_sep(menu)
            for row in self._MORE_SETTINGS:
                if row is None:
                    add_sep(menu)
                else:
                    add_to(menu, row[0], row[1])
            add_sep(menu)
            for row in self._MORE_INTERACT:
                if row is None:
                    add_sep(menu)
                else:
                    add_to(menu, row[0], row[1])
            add_sep(menu)
            add_to(menu, "开启极简模式", "toggle_simple_mode")
            add_to(menu, "退出", "quit")

        self._status_item.setMenu_(menu)
        self._mac_menu = menu

    def _build_win_menu(self):
        import pystray

        def on(cmd: str):
            def _handler(_icon=None, _item=None):
                self._emit(cmd)
            return _handler

        def items_from(rows: tuple):
            out = []
            for row in rows:
                if row is None:
                    out.append(pystray.Menu.SEPARATOR)
                else:
                    out.append(
                        pystray.MenuItem(self._menu_label(row[0], row[1]), on(row[1]))
                    )
            return out

        if self._simple():
            entries = [
                pystray.MenuItem(self._menu_label(t, c), on(c))
                for t, c in self._PRIMARY_SIMPLE
            ]
            entries += [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("更多互动", pystray.Menu(*items_from(self._MORE_INTERACT))),
                pystray.MenuItem("更多设置", pystray.Menu(*items_from(self._MORE_SETTINGS))),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("关闭极简模式", on("toggle_simple_mode")),
                pystray.MenuItem("退出", on("quit")),
            ]
        else:
            entries = [
                pystray.MenuItem("召唤过来", on("call")),
                pystray.MenuItem("摸头", on("pet")),
                pystray.MenuItem("投喂", on("feed")),
                pystray.MenuItem("纸箱", on("box")),
                pystray.MenuItem("睡觉", on("sleep")),
                pystray.MenuItem("系统总览", on("overview")),
                pystray.MenuItem(self._menu_label("状态", "status"), on("status")),
                pystray.Menu.SEPARATOR,
                *items_from(self._MORE_SETTINGS),
                pystray.Menu.SEPARATOR,
                *items_from(self._MORE_INTERACT),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("开启极简模式", on("toggle_simple_mode")),
                pystray.MenuItem("退出", on("quit")),
            ]
        return pystray.Menu(*entries)

    def _start_win_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as exc:
            print(f"⚠️ 托盘需要: pip install pystray pillow ({exc})")
            return

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((8, 16, 56, 52), fill=(60, 40, 30, 255))
        draw.ellipse((20, 10, 32, 22), fill=(60, 40, 30, 255))
        draw.ellipse((32, 10, 44, 22), fill=(60, 40, 30, 255))

        menu = self._build_win_menu()
        icon = pystray.Icon("cockroach_pet", img, "小猫桌宠", menu)
        self._tray_icon = icon
        print("系统托盘: 右键任务栏旁小猫图标（若在隐藏区点 ^ 展开）")
        try:
            icon.run()
        except Exception as exc:
            print(f"⚠️ 托盘运行失败: {exc}")
            self._tray_icon = None
