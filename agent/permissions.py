"""Three-level permission classification for agent tool calls."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from tools.path_security import workspace_path


Verdict = Literal["allow", "confirm", "deny"]

READONLY = {"read", "grep", "glob", "recall_memory", "todo_write", "update_todo", "insert_todo"}
WRITE = {"write", "edit"}
MEMORY_WRITE = {"remember", "forget_memory"}
EXEC = {"bash", "web_fetch"}


def check(tool: str, args: dict, workdir: Path) -> Verdict:
    """Classify one tool call without executing it."""
    if tool in READONLY:
        return "allow"
    if tool in WRITE:
        path = str(args.get("path") or "")
        if not path:
            return "deny"
        try:
            workspace_path(path, root=workdir)
        except (OSError, PermissionError):
            return "deny"
        return "confirm"
    if tool in MEMORY_WRITE:
        return "confirm"
    if tool in EXEC:
        return "confirm"
    return "confirm"
