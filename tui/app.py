"""mini-openclaw TUI 主应用。"""

from __future__ import annotations
from pathlib import Path
from typing import Callable

from textual.app import App
from textual.binding import Binding

from agent.runtime import AgentRuntime
from tui.screens import MainScreen


class MiniOpenClawApp(App):
    """Textual TUI 主应用。"""

    TITLE = "mini-openclaw"
    SUB_TITLE = "video knowledge terminal"
    # Textual 8.2 may dereference a detached Markdown paragraph when a mouse
    # selection starts during a streamed update. TextArea selection is exempt.
    ALLOW_SELECT = False

    CSS = (Path(__file__).parent / "styles.tcss").read_text(encoding="utf-8")

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit"),
    ]

    def __init__(self, runtime_factory: Callable[[], AgentRuntime] | None = None) -> None:
        super().__init__()
        self.runtime_factory = runtime_factory or (lambda: AgentRuntime(trace_prefix="tui"))

    def on_mount(self) -> None:
        """应用启动时进入主屏幕。"""
        self.push_screen(MainScreen(self.runtime_factory))
