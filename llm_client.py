"""OpenAI 兼容 LLM 客户端：DeepSeek / 豆包 / 千问。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

PROVIDER_ORDER = ("deepseek", "doubao", "qwen")

PERSONA = (
    "你是一只躲在电脑缝里的蟑螂桌宠，说话短、俏皮、略损。"
    "只用简体中文。不要解释、不要引号、不要表情符号、不要编号。"
)

_last_call: dict[str, float] = {}
_COOLDOWN_SEC = 8.0


class LLMError(Exception):
    pass


def ai_cfg(settings: dict[str, Any]) -> dict[str, Any]:
    cfg = settings.get("ai") or {}
    return cfg if isinstance(cfg, dict) else {}


def provider_ready(settings: dict[str, Any], name: str | None = None) -> bool:
    cfg = ai_cfg(settings)
    if not cfg.get("enabled"):
        return False
    pname = name or str(cfg.get("provider") or "deepseek")
    providers = cfg.get("providers") or {}
    p = providers.get(pname) or {}
    key = str(p.get("api_key") or "").strip()
    model = str(p.get("model") or "").strip()
    return bool(key and model)


def current_provider_name(settings: dict[str, Any]) -> str:
    cfg = ai_cfg(settings)
    name = str(cfg.get("provider") or "deepseek")
    return name if name in PROVIDER_ORDER else "deepseek"


def _check_cooldown(kind: str) -> None:
    now = time.time()
    last = _last_call.get(kind, 0.0)
    if now - last < _COOLDOWN_SEC:
        raise LLMError("冷却中")
    _last_call[kind] = now


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip().replace("\r", "")
    text = text.strip("「」\"'“”‘’ \n\t")
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def chat_completion(messages: list[dict[str, str]], settings: dict[str, Any]) -> str:
    cfg = ai_cfg(settings)
    pname = current_provider_name(settings)
    providers = cfg.get("providers") or {}
    p = providers.get(pname) or {}
    api_key = str(p.get("api_key") or "").strip()
    base_url = str(p.get("base_url") or "").rstrip("/")
    model = str(p.get("model") or "").strip()
    if not api_key or not base_url or not model:
        raise LLMError(f"{pname} 未配置 api_key/base_url/model")

    timeout = float(cfg.get("timeout_sec") or 12)
    url = f"{base_url}/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 220,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise LLMError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:
        raise LLMError(str(exc) or "网络错误") from exc

    try:
        payload = json.loads(raw)
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMError("响应解析失败") from exc
    return str(content or "").strip()


def generate_line(settings: dict[str, Any], kind: str = "chat", context: str = "") -> str:
    """生成单句气泡文案。"""
    _check_cooldown(kind)
    cfg = ai_cfg(settings)
    max_chars = int(cfg.get("max_chars") or 24)
    hint = {
        "chat": "随便冒一句缝里吐槽或关心打工人。",
        "showcase": "探头刷存在感，一句就够。",
        "poke": "被戳屁股/触角时的反应，可以凶一点。",
        "click": "被摸头时开心或撒娇一句。",
    }.get(kind, "说一句短吐槽。")
    user = f"{hint} 场景:{context or '桌面日常'}。只输出一句，不超过{max_chars}字。"
    text = chat_completion(
        [
            {"role": "system", "content": PERSONA},
            {"role": "user", "content": user},
        ],
        settings,
    )
    # 只取第一行
    line = text.splitlines()[0] if text else ""
    line = _truncate(line, max_chars)
    if not line:
        raise LLMError("空回复")
    return line


def generate_story_lines(settings: dict[str, Any]) -> list[str]:
    _check_cooldown("story")
    cfg = ai_cfg(settings)
    max_chars = min(20, int(cfg.get("max_chars") or 24))
    user = (
        f"写一个蟑螂桌宠小故事，正好5句，每句不超过{max_chars}字。"
        "题材可打工/debug/摸鱼/缝里日常。每行一句，不要标题。"
    )
    text = chat_completion(
        [
            {"role": "system", "content": PERSONA},
            {"role": "user", "content": user},
        ],
        settings,
    )
    lines: list[str] = []
    for raw in text.replace("|", "\n").splitlines():
        line = _truncate(raw.lstrip("0123456789.、)）-• "), max_chars)
        if line:
            lines.append(line)
        if len(lines) >= 6:
            break
    if len(lines) < 4:
        raise LLMError("故事太短")
    return lines[:6]


def generate_banter_script(settings: dict[str, Any]) -> list[tuple[str, str]]:
    """生成 main/buddy 交替 4 句对喷。"""
    _check_cooldown("banter")
    max_chars = 12
    user = (
        "写蟑螂主宠(main)与同伴(buddy)对喷，正好4句，交替发言，"
        f"每句不超过{max_chars}字。题材不限财务，要好玩。"
        "严格按格式每行: main|台词 或 buddy|台词。不要其它内容。"
    )
    text = chat_completion(
        [
            {"role": "system", "content": PERSONA},
            {"role": "user", "content": user},
        ],
        settings,
    )
    script: list[tuple[str, str]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if "|" not in raw:
            continue
        who, line = raw.split("|", 1)
        who = who.strip().lower()
        if who not in ("main", "buddy"):
            continue
        line = _truncate(line, max_chars)
        if line:
            script.append((who, line))
        if len(script) >= 4:
            break
    if len(script) < 4:
        raise LLMError("对喷解析失败")
    # 强制交替从 main 开始更好看
    return script[:4]
