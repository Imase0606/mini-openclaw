"""Cross-session project and user memory with safe persistence."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_NOTE_CHARS = 1000
MAX_KEY_CHARS = 80
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk|xai)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:api[_ -]?key|token|password|passwd|secret)\s*[:=]\s*\S+", re.I),
)
FORBIDDEN_BULK_MARKERS = ("# transcript_source:", "<external source=", "visual_notes.jsonl")


def _validate_memory_text(text: str) -> str:
    clean = " ".join(str(text or "").strip().split())
    if not clean:
        raise ValueError("记忆内容不能为空")
    if len(clean) > MAX_NOTE_CHARS:
        raise ValueError(f"单条记忆不能超过 {MAX_NOTE_CHARS} 字符")
    if any(marker.lower() in clean.lower() for marker in FORBIDDEN_BULK_MARKERS):
        raise ValueError("禁止把 transcript、OCR 或外部内容整段写入长期记忆")
    if any(pattern.search(clean) for pattern in SECRET_PATTERNS):
        raise ValueError("记忆疑似包含密钥、密码或令牌，已拒绝持久化")
    return clean


def _query_terms(text: str) -> set[str]:
    lowered = text.lower()
    ascii_tokens = re.findall(r"[a-z0-9_.-]{2,}", lowered)
    terms = set(ascii_tokens) | set(re.findall(r"[\u4e00-\u9fff]{2,}", lowered))
    for token in ascii_tokens:
        terms.update(part for part in re.split(r"[_.-]+", token) if len(part) >= 2)
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    terms.update(chinese[i:i + 2] for i in range(max(0, len(chinese) - 1)))
    return terms


class Memory:
    """Append-only Markdown project memory required by the course interface."""

    def __init__(self, path: str | Path = "MEMORY.md") -> None:
        self.path = Path(path)

    def write(self, note: str) -> None:
        clean = _validate_memory_text(note)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(existing + f"- {clean}\n", encoding="utf-8")
        temporary.replace(self.path)

    def recall(self, query: str = "", max_chars: int = 4000) -> str:
        if not self.path.is_file():
            return ""
        text = self.path.read_text(encoding="utf-8", errors="ignore")
        if not query.strip() or len(text) <= max_chars:
            return text[:max_chars]
        terms = _query_terms(query)
        lines = text.splitlines()
        selected = [line for line in lines if _query_terms(line) & terms]
        return "\n".join(selected)[:max_chars]


class KVMemory:
    """Structured runtime memory supporting overwrite, recall and forgetting."""

    def __init__(self, path: str | Path = ".mini-openclaw/memory.json") -> None:
        self.path = Path(path)
        self.data: dict[str, dict[str, str]] = self._load()

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.is_file():
            return {}
        try:
            raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, dict[str, str]] = {}
        for key, item in raw.items():
            if isinstance(item, dict) and isinstance(item.get("value"), str):
                normalized[str(key)] = {
                    "value": item["value"],
                    "updated_at": str(item.get("updated_at") or ""),
                }
            elif isinstance(item, str):
                normalized[str(key)] = {"value": item, "updated_at": ""}
        return normalized

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def remember(self, key: str, value: str) -> None:
        clean_key = " ".join(str(key or "").strip().split())
        if not clean_key or len(clean_key) > MAX_KEY_CHARS:
            raise ValueError(f"记忆 key 必须为 1-{MAX_KEY_CHARS} 个字符")
        clean_value = _validate_memory_text(value)
        self.data[clean_key] = {
            "value": clean_value,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save()

    def forget(self, key: str) -> bool:
        removed = self.data.pop(str(key or "").strip(), None) is not None
        if removed:
            self._save()
        return removed

    def recall(self, query: str = "", max_items: int = 8, max_chars: int = 3000) -> str:
        entries = list(self.data.items())
        if query.strip():
            terms = _query_terms(query)
            scored = []
            for key, item in entries:
                haystack = f"{key} {item['value']}"
                score = len(terms & _query_terms(haystack))
                if score:
                    scored.append((score, key, item))
            entries = [(key, item) for _score, key, item in sorted(scored, reverse=True)]
        rendered = [f"- **{key}**: {item['value']}" for key, item in entries[:max_items]]
        return "\n".join(rendered)[:max_chars]
