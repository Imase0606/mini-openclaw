"""mini-openclaw TUI 主应用。"""

from __future__ import annotations
from pathlib import Path

from textual.app import App
from textual.binding import Binding

from tui.screens import MainScreen
from tui.status_bar import LOGO_ART


class MiniOpenClawApp(App):
    """Textual TUI 主应用。"""

    TITLE = "mini-openclaw"
    SUB_TITLE = "惊讶！让 AI 帮你搞定任务！"

    CSS = (Path(__file__).parent / "styles.tcss").read_text(encoding="utf-8")

    BINDINGS = [
        Binding("ctrl+c", "quit", "退出"),
        Binding("escape", "cancel", "取消"),
    ]

    SCREENS = {
        "main": MainScreen,
    }

    def on_mount(self) -> None:
        """应用启动时进入主屏幕。"""
        self.push_screen("main")
