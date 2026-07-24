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
    "sys_alerts": True,
    "sys_check_interval_min": 50,
    "sys_check_interval_max": 90,
    "click_through_force": False,
    "follow_default": False,
    "global_hotkeys": True,
    "accountant_buddy": True,
    "buddy_banter_min": 120,
    "buddy_banter_max": 280,
    "idle_showcase": True,
    "idle_showcase_min": 45,
    "idle_showcase_max": 90,
    "rest_reminder": True,
    "rest_reminder_interval_sec": 3600,
    # 鼠标长时间不动时，小猫跑去找指针互动
    "mouse_seek": True,
    "mouse_idle_sec": 1800,
    "mouse_seek_cooldown_sec": 900,
    "enabled_packs": ["worker", "finance", "programmer", "accountant"],
    "skin": "default",
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
