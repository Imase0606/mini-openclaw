"""Slash-command and workspace-file completion widgets."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option


COMMANDS = {
    "/help": "Browse commands and shortcuts",
    "/new": "Start a new session",
    "/clear": "Clear the current context",
    "/sessions": "List recent sessions",
    "/resume": "Resume a saved session",
    "/compact": "Compact conversation context",
    "/model": "Switch configured model",
    "/permissions": "Change permission mode",
    "/plan": "Set planning mode",
    "/video-type": "Set video note type",
    "/bilibili-login": "Scan QR for built-in subtitles",
    "/bilibili-status": "Check Bilibili subtitle login",
    "/bilibili-logout": "Clear this Runtime's Bilibili login",
    "/image": "Attach an image",
    "/images": "List pending images",
    "/trace": "Show trace details",
    "/cost": "Show token and cost summary",
    "/open": "Open a generated artifact",
    "/quit": "Exit mini-openclaw",
}


class CompletionMenu(OptionList):
    def __init__(self) -> None:
        super().__init__(id="completion-menu", compact=True)
        self.values: list[str] = []
        self.display = False

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self.clear_options()
        self.values = [value for value, _label in items]
        if items:
            self.add_options([Option(Text(label), id=str(index)) for index, (_value, label) in enumerate(items)])
            self.highlighted = 0
            self.display = True
        else:
            self.display = False

    def selected_value(self) -> str:
        if not self.values:
            return ""
        index = self.highlighted if self.highlighted is not None else 0
        return self.values[max(0, min(index, len(self.values) - 1))]


def command_suggestions(text: str, limit: int = 8) -> list[tuple[str, str]]:
    query = text.strip().lower()
    if not query.startswith("/") or " " in query:
        return []
    matches = []
    for command, description in COMMANDS.items():
        if _fuzzy_match(query, command):
            matches.append((command, f"{command:<18} {description}"))
    return matches[:limit]


def file_query(text: str) -> str | None:
    match = re.search(r"(?:^|\s)@([^\s]*)$", text)
    return match.group(1) if match else None


def file_suggestions(query: str, files: list[str], limit: int = 8) -> list[tuple[str, str]]:
    needle = query.lower()
    matched = [path for path in files if _fuzzy_match(needle, path.lower())]
    return [(f"@{path}", f"@{path}") for path in matched[:limit]]


def workspace_files(root: Path | None = None, limit: int = 5000) -> list[str]:
    workspace = (root or Path.cwd()).resolve()
    command = [
        "rg", "--files", "--hidden",
        "-g", "!.git/**", "-g", "!.venv/**", "-g", "!.mini-openclaw/**",
        "-g", "!knowledge_base/**", "-g", "!*.key", "-g", "!*.pem", "-g", "!.env*",
    ]
    try:
        result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=5, check=False)
        files = result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        files = [str(path.relative_to(workspace)) for path in workspace.rglob("*") if path.is_file()]
    return sorted(path.replace("\\", "/") for path in files[:limit])


def replace_completion(text: str, value: str) -> str:
    if text.lstrip().startswith("/") and " " not in text.strip():
        return value + " "
    match = re.search(r"@[^\s]*$", text)
    if match:
        return text[:match.start()] + value + " "
    return text


def _fuzzy_match(query: str, candidate: str) -> bool:
    if not query:
        return True
    iterator = iter(candidate)
    return all(any(char == current for current in iterator) for char in query)
