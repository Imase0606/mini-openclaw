"""Open explicitly selected workspace artifacts with the platform handler."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def resolve_artifact(path: str, root: Path | None = None) -> Path:
    workspace = (root or Path.cwd()).resolve()
    candidate = (workspace / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        raise ValueError("refused to open a file outside the workspace") from None
    if not candidate.is_file():
        raise FileNotFoundError(f"file does not exist: {candidate}")
    return candidate


def open_artifact(path: str, root: Path | None = None) -> tuple[bool, str]:
    try:
        candidate = resolve_artifact(path, root)
    except (OSError, ValueError) as exc:
        return False, str(exc)
    try:
        if os.name == "nt":
            os.startfile(candidate)  # type: ignore[attr-defined]
        else:
            opener = shutil.which("wslview") or shutil.which("xdg-open")
            if not opener:
                return False, f"no system opener is available: {candidate}"
            subprocess.Popen(
                [opener, str(candidate)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError as exc:
        return False, f"open failed: {exc}"
    return True, str(candidate)
