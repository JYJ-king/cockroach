#!/usr/bin/env python3
"""从 awesome-codex-pet 拉取猫咪形象到 codex_pets/。

来源: https://github.com/legeling/awesome-codex-pet
素材默认 CC BY-NC 4.0（以各 pet 的 submission.json.license 为准）。
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "codex_pets"
CATALOG_URL = "https://raw.githubusercontent.com/legeling/awesome-codex-pet/main/pets.json"
RAW = "https://raw.githubusercontent.com/legeling/awesome-codex-pet/main/pets"

# 关键词命中但非猫形象
DENY = {
    "doraemon--xueshi",
    "dimo-stand--god-wu",
    "misaka-network--ldl1234",
    "violet--lazenca",
    "yume-boundary--andy-meow",  # person
    "kuro-chibi--kuroneko-night",  # catgirl
}

KW = re.compile(
    r"cat|kitten|kitty|neko|meow|喵|猫|猫咪|小猫|哈基米|耄耋|橘猫|布偶|狸花|shorthair",
    re.I,
)


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def main() -> None:
    pets = json.loads(fetch(CATALOG_URL).decode())
    selected = []
    for p in pets:
        slug = p["slug"]
        if slug in DENY:
            continue
        blob = " ".join(
            [
                slug,
                p.get("name") or "",
                p.get("description") or "",
                str(p.get("localized_names") or {}),
                " ".join(p.get("collections") or []),
                p.get("primary_category") or "",
            ]
        )
        if KW.search(blob):
            selected.append(p)

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for p in selected:
        slug = p["slug"]
        dest = OUT / slug
        dest.mkdir(parents=True, exist_ok=True)
        ok = True
        for fname in ("spritesheet.webp", "pet.json", "submission.json"):
            path = dest / fname
            try:
                path.write_bytes(fetch(f"{RAW}/{slug}/{fname}"))
                print(f"ok {slug}/{fname}")
            except Exception as exc:
                print(f"FAIL {slug}/{fname}: {exc}")
                if fname == "spritesheet.webp":
                    ok = False
                    break
        if not ok:
            continue
        meta = json.loads((dest / "pet.json").read_text(encoding="utf-8"))
        # 排除 kind=person
        if str(meta.get("kind") or "").lower() == "person":
            print(f"skip person {slug}")
            continue
        sub = {}
        if (dest / "submission.json").exists():
            sub = json.loads((dest / "submission.json").read_text(encoding="utf-8"))
        desc = (meta.get("description") or p.get("description") or "").lower()
        if "catgirl" in desc and "cat " not in desc and "猫" not in (meta.get("description") or ""):
            # 猫娘但描述不像猫本体时跳过（已在 DENY 处理主流）
            pass
        manifest.append(
            {
                "slug": slug,
                "name": meta.get("displayName") or p.get("name") or slug,
                "name_zh": (p.get("localized_names") or {}).get("zh")
                or meta.get("displayName")
                or p.get("name"),
                "author": sub.get("author") or p.get("author") or "",
                "author_handle": sub.get("author_handle") or p.get("author_handle") or "",
                "license": sub.get("license") or p.get("license") or "CC BY-NC 4.0",
                "spriteVersionNumber": meta.get(
                    "spriteVersionNumber", p.get("spriteVersionNumber", 1)
                ),
                "source": f"https://github.com/legeling/awesome-codex-pet/tree/main/pets/{slug}",
            }
        )

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"done: {len(manifest)} cats -> {OUT}")


if __name__ == "__main__":
    main()
