"""外置话术包：packs/*.json，可热加载。"""

from __future__ import annotations

import json
import os
import random
from typing import Any


# 分类键 ↔ JSON phrases 字段
CATEGORY_KEYS = (
    "worker_buzz",
    "worker_tips_weekday",
    "worker_tips_monday",
    "worker_tips_friday",
    "worker_tips_weekend",
    "worker_standup",
    "worker_align",
    "worker_review",
    "worker_fish",
    "worker_pua",
    "finance_buzz",
    "finance_catchphrases",
    "finance_tips",
    "finance_close",
    "finance_audit",
    "finance_reimburse",
    "finance_tax",
    "finance_payroll",
    "programmer_buzz",
    "festival",
    "chat",
    "care_eye",
    "care_water",
    "care_stretch",
    "care_rest",
    "support_close",
    "meeting_end",
    "focus_done",
    "focus_done_quiet",
    "focus_done_neutral",
)


class PackManager:
    def __init__(self) -> None:
        self.packs: dict[str, dict[str, Any]] = {}
        self.enabled: list[str] = []
        self._merged: dict[str, list[str]] = {}

    def load(self, packs_dir: str, enabled_ids: list[str] | None = None) -> None:
        self.packs.clear()
        if os.path.isdir(packs_dir):
            for name in sorted(os.listdir(packs_dir)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(packs_dir, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        continue
                    pid = str(data.get("id") or os.path.splitext(name)[0])
                    self.packs[pid] = data
                except (OSError, json.JSONDecodeError) as exc:
                    print(f"⚠️ 话术包加载失败 {name}: {exc}")
        self.enabled = [i for i in (enabled_ids or []) if i in self.packs]
        # 未指定时启用全部
        if enabled_ids is None:
            self.enabled = list(self.packs.keys())
        self._rebuild()

    def set_enabled(self, enabled_ids: list[str]) -> None:
        self.enabled = [i for i in enabled_ids if i in self.packs]
        self._rebuild()

    def _rebuild(self) -> None:
        merged: dict[str, list[str]] = {k: [] for k in CATEGORY_KEYS}
        for pid in self.enabled:
            phrases = (self.packs.get(pid) or {}).get("phrases") or {}
            if not isinstance(phrases, dict):
                continue
            for key, vals in phrases.items():
                if key not in merged or not isinstance(vals, list):
                    continue
                for v in vals:
                    s = str(v).strip()
                    if s and s not in merged[key]:
                        merged[key].append(s)
        self._merged = merged

    def extras(self, category: str) -> list[str]:
        return list(self._merged.get(category) or [])

    def pool(self, category: str, fallback: list[str]) -> list[str]:
        extra = self.extras(category)
        if not extra:
            return list(fallback)
        # 内置 + 外置合并，外置稍加权：多拷一份
        return list(fallback) + extra + extra

    def pick(self, category: str, fallback: list[str]) -> str:
        pool = self.pool(category, fallback)
        if not pool:
            return "…"
        return random.choice(pool)

    def list_packs(self) -> list[tuple[str, str, bool]]:
        rows = []
        for pid, data in sorted(self.packs.items()):
            name = str(data.get("name") or pid)
            rows.append((pid, name, pid in self.enabled))
        return rows


PACKS = PackManager()
