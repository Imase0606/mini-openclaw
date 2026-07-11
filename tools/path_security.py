"""Workspace path validation shared by filesystem-facing tools."""
from __future__ import annotations

from pathlib import Path


PROTECTED_DIRS = {".git", ".ssh"}
PROTECTED_SUFFIXES = {".key", ".pem"}


def _is_protected(relative: Path) -> bool:
    names = {part.lower() for part in relative.parts}
    if names & PROTECTED_DIRS:
        return True
    name = relative.name.lower()
    return name == ".env" or name.startswith(".env.") or relative.suffix.lower() in PROTECTED_SUFFIXES


def workspace_path(
    path: str | Path,
    *,
    protect_secrets: bool = True,
    root: str | Path | None = None,
) -> Path:
    """Resolve a path inside the current workspace and reject protected targets."""
    raw = Path(path)
    workspace = Path(root).resolve() if root is not None else Path.cwd().resolve()
    resolved = (raw if raw.is_absolute() else workspace / raw).resolve(strict=False)
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError(f"路径越过工作区边界：{path}") from exc
    if protect_secrets and _is_protected(relative):
        raise PermissionError(f"拒绝访问受保护路径：{path}")
    return resolved


def is_safe_workspace_file(path: Path) -> bool:
    """Return whether an existing file is safe to expose through discovery tools."""
    try:
        workspace_path(path)
    except (OSError, PermissionError):
        return False
    return path.is_file()
