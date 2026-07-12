"""Redacted, workspace-scoped TUI session persistence."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.tracer import redact_text


SESSION_VERSION = 1
MAX_MESSAGE_CHARS = 20_000
MAX_HISTORY_MESSAGES = 80
MAX_SESSION_BYTES = 1_000_000


@dataclass
class SessionRecord:
    session_id: str
    cwd: str
    created_at: str
    updated_at: str
    title: str
    history: list[dict[str, Any]]
    settings: dict[str, Any]
    artifacts: list[dict[str, str]]


class SessionStore:
    def __init__(self, root: str | Path = ".mini-openclaw/sessions", workdir: Path | None = None) -> None:
        self.root = Path(root)
        self.workdir = (workdir or Path.cwd()).resolve()

    def save(
        self,
        session_id: str,
        history: list[dict[str, Any]],
        *,
        settings: dict[str, Any] | None = None,
        artifacts: list[dict[str, str]] | None = None,
        created_at: str = "",
    ) -> Path:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        safe_history = [_sanitize_message(item) for item in history[-MAX_HISTORY_MESSAGES:]]
        safe_history = [item for item in safe_history if item]
        title = _session_title(safe_history)
        payload = {
            "version": SESSION_VERSION,
            "session_id": session_id,
            "cwd": str(self.workdir),
            "created_at": created_at or now,
            "updated_at": now,
            "title": title,
            "history": safe_history,
            "settings": _safe_settings(settings or {}),
            "artifacts": _safe_artifacts(artifacts or [], self.workdir),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        while len(text.encode("utf-8")) > MAX_SESSION_BYTES and len(payload["history"]) > 2:
            payload["history"].pop(0)
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{session_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        return path

    def load(self, session_id: str) -> SessionRecord:
        if not session_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in session_id):
            raise ValueError("无效会话 ID")
        path = self.root / f"{session_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"会话无法读取：{exc}") from exc
        if payload.get("version") != SESSION_VERSION or Path(str(payload.get("cwd") or "")).resolve() != self.workdir:
            raise ValueError("会话版本不兼容或不属于当前工作区")
        return SessionRecord(
            session_id=str(payload["session_id"]),
            cwd=str(payload["cwd"]),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            title=str(payload.get("title") or "未命名会话"),
            history=list(payload.get("history") or []),
            settings=dict(payload.get("settings") or {}),
            artifacts=list(payload.get("artifacts") or []),
        )

    def list(self, limit: int = 20) -> list[SessionRecord]:
        if not self.root.is_dir():
            return []
        records: list[SessionRecord] = []
        for path in self.root.glob("*.json"):
            try:
                record = self.load(path.stem)
            except ValueError:
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.updated_at, reverse=True)[:limit]


def _sanitize_message(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role") or "")
    if role not in {"user", "assistant", "tool", "system"}:
        return {}
    if role == "system" and not message.get("_history_memo"):
        return {}
    content = message.get("content", "")
    if isinstance(content, list):
        blocks = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                blocks.append({"type": "text", "text": redact_text(block.get("text"), MAX_MESSAGE_CHARS)})
            elif block.get("type") in {"image", "image_url"}:
                blocks.append({"type": "text", "text": "[image omitted from persisted session]"})
        safe_content: Any = blocks
    else:
        safe_content = redact_text(content, MAX_MESSAGE_CHARS)
    safe = {"role": role, "content": safe_content}
    for key in ("name", "tool_call_id", "tool_calls", "_history_memo"):
        if key in message:
            safe[key] = message[key] if key != "tool_calls" else _sanitize_tool_calls(message[key])
    return safe


def _sanitize_tool_calls(calls: Any) -> list[dict[str, Any]]:
    output = []
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, dict):
            continue
        output.append({
            "id": str(call.get("id") or ""),
            "name": str(call.get("name") or ""),
            "arguments": {key: redact_text(value, 500) for key, value in (call.get("arguments") or {}).items()},
        })
    return output


def _safe_settings(settings: dict[str, Any]) -> dict[str, Any]:
    allowed = {"planning_mode", "video_type", "permission_mode", "model_alias"}
    return {key: str(value) for key, value in settings.items() if key in allowed}


def _safe_artifacts(artifacts: list[dict[str, str]], workdir: Path) -> list[dict[str, str]]:
    output = []
    for item in artifacts[:100]:
        path = Path(str(item.get("path") or ""))
        resolved = (workdir / path).resolve() if not path.is_absolute() else path.resolve()
        try:
            relative = resolved.relative_to(workdir)
        except ValueError:
            continue
        output.append({"kind": str(item.get("kind") or "file"), "path": str(relative)})
    return output


def _session_title(history: list[dict[str, Any]]) -> str:
    for message in history:
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
        return " ".join(str(content).split())[:80] or "未命名会话"
    return "未命名会话"
