"""本地设置与进度（成就/亲密度快照）。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "bubbles_enabled": True,
    "worker_reminders": True,
    "finance_reminders": True,
    # 财务脏话：菜单/快捷键可喷；也可夹进行话池（应援模式不夹）
    "finance_swear": True,
    "sys_alerts": True,
    "sys_check_interval_min": 50,
    "sys_check_interval_max": 90,
    "click_through_force": False,
    "follow_default": False,
    "global_hotkeys": True,
    # 极简模式：菜单只留常用互动，其余进「更多」；快捷键也收窄
    "simple_mode": True,
    "accountant_buddy": True,
    "buddy_banter_min": 120,
    "buddy_banter_max": 280,
    # 月结应援：用户标记或自动识别高压日时，对喷降频并改鼓励话术
    "buddy_support_mode": False,
    "buddy_auto_support": True,
    # 用户习惯的月结日（1–28）；0=未设置，仍用月初/月末启发式
    "close_day": 0,
    # 用户自选的月结忙碌窗口（1–31）；均为 0 表示未设，走 close_day / 月初月末启发式
    "close_window_start_day": 0,
    "close_window_end_day": 0,
    "idle_showcase": True,
    # 屏幕自主移动/周期表演：引导可选 5/10/30/60 分钟，默认 5 分钟
    "interaction_interval_sec": 300,
    # 相位错开：周期表演偏短窗(0.9~1.0×)，自主行为偏长窗(1.0~1.1×)
    "idle_showcase_min": 270,
    "idle_showcase_max": 300,
    "rest_reminder": True,
    "rest_reminder_interval_sec": 3600,
    # 护眼/喝水/伸展：轻量气泡提醒，间隔可在 settings 改（秒）
    "care_reminders": True,
    "care_preset": "standard",  # gentle | standard | strict | custom
    "care_eye_sec": 1200,       # 默认 20 分钟（20-20-20）
    "care_water_sec": 1800,     # 默认 30 分钟
    "care_stretch_sec": 2700,   # 默认 45 分钟
    # 专注番茄钟：手动开倒计时；结束是否催休息受 rest_reminder 约束
    "focus_pomodoro_sec": 1500,
    # 无人操作时自主散步/发呆/瞌睡/攀爬/倒挂
    "autonomy": True,
    "autonomy_idle_sec": 45,
    "autonomy_min": 300,
    "autonomy_max": 330,
    "autonomy_respect_focus": True,
    # 会议/共享/投屏/截图：共享与投屏收起；截图热键躲闪；开会安静
    "meeting_silence": True,
    # 鼠标长时间不动时，小猫跑去找指针互动
    "mouse_seek": True,
    "mouse_idle_sec": 1800,
    "mouse_seek_cooldown_sec": 900,
    "enabled_packs": ["worker", "finance", "programmer", "accountant", "wellness"],
    "skin": "default",
    # 形象锁定：true 时下次启动用 appearance_slug，不再随机
    "appearance_lock": False,
    "appearance_slug": "",
    "ai": {
        "enabled": False,
        "provider": "deepseek",
        "timeout_sec": 12,
        "max_chars": 24,
        "providers": {
            "deepseek": {
                "api_key": "",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
            },
            "doubao": {
                "api_key": "",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "",
            },
            "qwen": {
                "api_key": "",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-turbo",
            },
        },
    },
    "hotkeys": {
        "call": "<ctrl>+<alt>+r",
        "overview": "<ctrl>+<alt>+/",
        "passthrough": "<ctrl>+<alt>+p",
        "quit": "<ctrl>+<alt>+q",
        "status": "<ctrl>+<alt>+s",
        "banter": "<ctrl>+<alt>+b",
        "story": "<ctrl>+<alt>+t",
        "ai": "<ctrl>+<alt>+a",
    },
}

DEFAULT_PROGRESS: dict[str, Any] = {
    "affection": 0,
    "pet_count": 0,
    "feed_count": 0,
    "call_count": 0,
    "sys_alert_count": 0,
    "banter_count": 0,
    "story_count": 0,
    "titles": [],
    "unlocked_skins": ["default"],
    "stats": {},
    # 首次启动气泡引导是否完成（兼容旧键 onboarding_done）
    "onboarding_completed": False,
    "onboarding_done": False,
    # 月结应援误判反馈：用于以后调日历/负载阈值
    "support_feedback": [],
    "support_fp_stats": {},
    # 用户点「这次不是月结」后，自动应援抑制到此时刻（unix）
    "support_dismiss_until": 0.0,
    # 应援窗口已结束，等待下一次回家/睡觉做收工仪式
    "support_close_pending": False,
}

ACHIEVEMENTS: list[dict[str, Any]] = [
    {"id": "first_pet", "title": "初次摸摸", "desc": "摸头 1 次", "check": lambda p: p.get("pet_count", 0) >= 1},
    {"id": "pet_50", "title": "好感达人", "desc": "累计摸头 50 次", "check": lambda p: p.get("pet_count", 0) >= 50},
    {"id": "feed_10", "title": "投喂选手", "desc": "喂食 10 次", "check": lambda p: p.get("feed_count", 0) >= 10},
    {"id": "call_5", "title": "一呼即来", "desc": "召唤 5 次", "check": lambda p: p.get("call_count", 0) >= 5},
    {"id": "alert_3", "title": "监控哨兵", "desc": "收到 3 次资源告警", "check": lambda p: p.get("sys_alert_count", 0) >= 3},
    {"id": "aff_20", "title": "窗台挚友", "desc": "亲密度达到 20", "check": lambda p: p.get("affection", 0) >= 20},
    {"id": "aff_100", "title": "桌宠知己", "desc": "亲密度达到 100", "check": lambda p: p.get("affection", 0) >= 100},
    {"id": "banter_5", "title": "对账搭子", "desc": "与会计猫对喷 5 次", "check": lambda p: p.get("banter_count", 0) >= 5},
    {"id": "story_3", "title": "说书小猫", "desc": "听完故事大会 3 次", "check": lambda p: p.get("story_count", 0) >= 3},
]


def settings_path(app_dir: str) -> str:
    return os.path.join(app_dir, "settings.json")


def secrets_path(app_dir: str) -> str:
    return os.path.join(app_dir, "secrets.json")


def progress_path(app_dir: str) -> str:
    return os.path.join(app_dir, "progress.json")


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def save_provider_api_key(app_dir: str, provider: str, api_key: str) -> None:
    """把指定厂商的 api_key 写入 secrets.json（不进 settings.json）。"""
    path = secrets_path(app_dir)
    data: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError):
            data = {}
    ai = data.setdefault("ai", {})
    if not isinstance(ai, dict):
        ai = {}
        data["ai"] = ai
    providers = ai.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        ai["providers"] = providers
    entry = providers.setdefault(str(provider), {})
    if not isinstance(entry, dict):
        entry = {}
        providers[str(provider)] = entry
    entry["api_key"] = str(api_key or "").strip()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as exc:
        print(f"⚠️ 无法保存 secrets.json: {exc}")
        raise


def apply_interaction_interval(settings: dict[str, Any], sec: int | None = None) -> dict[str, Any]:
    """写入引导「互动频率」主字段，并同步自主/周期表演窗口（相位错开）。

    - idle_showcase：interaction_interval_sec × (0.9~1.0)
    - autonomy：interaction_interval_sec × (1.0~1.1)
    避免两者共用同一随机区间导致前后脚撞车。
    """
    if sec is None:
        sec = int(settings.get("interaction_interval_sec") or 300)
    sec = max(300, min(3600, int(sec)))
    sc_lo = max(60, int(sec * 0.9))
    sc_hi = max(sc_lo + 1, int(sec * 1.0))
    au_lo = max(60, int(sec * 1.0))
    au_hi = max(au_lo + 1, int(sec * 1.1))
    settings["interaction_interval_sec"] = sec
    settings["idle_showcase_min"] = sc_lo
    settings["idle_showcase_max"] = sc_hi
    settings["autonomy_min"] = au_lo
    settings["autonomy_max"] = au_hi
    return settings


def _load_secrets_overlay(app_dir: str) -> dict[str, Any]:
    """可选 secrets.json：仅覆盖 ai.providers.*.api_key 等敏感项，勿提交仓库。"""
    path = secrets_path(app_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_settings(app_dir: str) -> dict[str, Any]:
    path = settings_path(app_dir)
    if not os.path.isfile(path):
        data = deepcopy(DEFAULT_SETTINGS)
        save_settings(app_dir, data)
        return _deep_merge(data, _load_secrets_overlay(app_dir))
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return deepcopy(DEFAULT_SETTINGS)
        merged = _deep_merge(DEFAULT_SETTINGS, raw)
        # 旧配置补上养生话术包（不覆盖用户其它包选择）
        ep = merged.get("enabled_packs")
        if isinstance(ep, list) and "wellness" not in ep:
            merged["enabled_packs"] = list(ep) + ["wellness"]
        if "interaction_interval_sec" not in raw:
            apply_interaction_interval(merged, 300)
        else:
            apply_interaction_interval(merged, merged.get("interaction_interval_sec"))
        return _deep_merge(merged, _load_secrets_overlay(app_dir))
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_SETTINGS)


def save_settings(app_dir: str, data: dict[str, Any]) -> None:
    path = settings_path(app_dir)
    to_save = deepcopy(data)
    # 若存在 secrets.json，不把内存里的 api_key 写回 settings.json
    if os.path.isfile(secrets_path(app_dir)):
        ai = to_save.get("ai")
        if isinstance(ai, dict):
            providers = ai.get("providers") or {}
            if isinstance(providers, dict):
                for p in providers.values():
                    if isinstance(p, dict) and "api_key" in p:
                        p["api_key"] = ""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"⚠️ 无法保存设置: {exc}")


def load_progress(app_dir: str) -> dict[str, Any]:
    path = progress_path(app_dir)
    if not os.path.isfile(path):
        return deepcopy(DEFAULT_PROGRESS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return deepcopy(DEFAULT_PROGRESS)
        merged = _deep_merge(DEFAULT_PROGRESS, raw)
        # 引导完成标记：优先 onboarding_completed，兼容 onboarding_done
        if "onboarding_completed" in raw:
            merged["onboarding_completed"] = bool(raw.get("onboarding_completed"))
            merged["onboarding_done"] = merged["onboarding_completed"]
        elif "onboarding_done" in raw:
            merged["onboarding_completed"] = bool(raw.get("onboarding_done"))
            merged["onboarding_done"] = merged["onboarding_completed"]
        else:
            # 旧进度无引导字段：有互动记录则视为已完成，避免老用户被突然引导
            activity = (
                int(raw.get("pet_count") or 0)
                + int(raw.get("feed_count") or 0)
                + int(raw.get("call_count") or 0)
                + int(raw.get("banter_count") or 0)
                + int(raw.get("story_count") or 0)
            )
            done = activity > 0 or bool(raw.get("titles"))
            merged["onboarding_completed"] = done
            merged["onboarding_done"] = done
        # 旧蟑螂称号 → 小猫称号
        rename = {
            "缝里挚友": "窗台挚友",
            "缝里说书人": "说书小猫",
        }
        titles = list(merged.get("titles") or [])
        merged["titles"] = [rename.get(t, t) for t in titles]
        return merged
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_PROGRESS)


def save_progress(app_dir: str, data: dict[str, Any]) -> None:
    path = progress_path(app_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"⚠️ 无法保存进度: {exc}")


def evaluate_achievements(progress: dict[str, Any]) -> list[str]:
    """检查新成就，写入 titles，返回新解锁称号列表。"""
    have = set(progress.get("titles") or [])
    newly: list[str] = []
    for ach in ACHIEVEMENTS:
        aid = ach["id"]
        title = ach["title"]
        if title in have or aid in have:
            continue
        try:
            ok = bool(ach["check"](progress))
        except Exception:
            ok = False
        if ok:
            have.add(title)
            newly.append(title)
    progress["titles"] = sorted(have)
    # 成就解锁皮肤
    unlocked = set(progress.get("unlocked_skins") or ["default"])
    if "好感达人" in have:
        unlocked.add("gold")
    if "桌宠知己" in have:
        unlocked.add("ghost")
    progress["unlocked_skins"] = sorted(unlocked)
    return newly
