"""顶部状态栏：logo + 状态指示 + token 计数。"""

from __future__ import annotations
from textual.widgets import Static
from textual.containers import Horizontal
from textual.app import ComposeResult

# ── Logo ──
# 拉长的括号 [ ]，^ ^ 在上半部分，括号上下边更细
LOGO_ART = r"""
[bold cyan]███   █       █   ███
█    █ █     █ █    █
█   █   █   █   █   █
█                   █
█                   █
███               ███[/]
[bold yellow]    mini-openclaw[/]
"""

# 更简洁的横向版本（用于状态栏旁边）
LOGO_COMPACT = "[cyan][ ^ ^ ][/cyan] [bold]mini-openclaw[/bold]"

STATUS_STYLES = {
    "idle": "dim",
    "thinking": "bold cyan",
    "executing": "bold yellow",
    "awaiting_permission": "bold magenta",
    "done": "bold green",
    "error": "bold red",
    "cancelled": "dim red",
}

STATUS_ICONS = {
    "idle": "●",
    "thinking": "⟳",
    "executing": "⚡",
    "awaiting_permission": "?",
    "done": "✓",
    "error": "✗",
    "cancelled": "⊘",
}


class StatusBar(Horizontal):
    """顶部状态栏：agent 状态 + 轮次 + token 计数。"""

    def __init__(self):
        super().__init__()
        self._logo = Static(LOGO_COMPACT)
        self._status_widget = Static("[dim]● idle[/dim]")
        self._turn_widget = Static("Turn: 0")
        self._token_widget = Static("Tokens: --")

    def compose(self) -> ComposeResult:
        yield self._logo
        yield Static("│")
        yield self._status_widget
        yield Static("│")
        yield self._turn_widget
        yield Static("│")
        yield self._token_widget

    def set_status(self, status: str, tool_name: str = "") -> None:
        icon = STATUS_ICONS.get(status, "●")
        style = STATUS_STYLES.get(status, "dim")
        text = f"{icon} {status}"
        if tool_name:
            text += f" ({tool_name})"
        self._status_widget.update(f"[{style}]{text}[/{style}]")

    def set_turn(self, turn: int) -> None:
        self._turn_widget.update(f"Turn: {turn}")

    def set_token_usage(self, prompt: int, completion: int) -> None:
        total = prompt + completion
        self._token_widget.update(f"Tokens: ↑{prompt} ↓{completion} Σ{total}")
