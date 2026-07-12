"""多行输入框。Enter 提交，Shift+Enter 换行。"""

from __future__ import annotations
from textual.widgets import TextArea
from textual.message import Message
from textual.binding import Binding
from textual.app import ComposeResult
from textual.containers import Horizontal


class PromptInput(TextArea):
    """多行输入框，底部停靠。

    按键:
      Enter       — 提交任务
      Shift+Enter — 插入换行
      Ctrl+C      — 退出
    """

    BINDINGS = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("tab", "complete", "Complete", priority=True),
    ]

    class Submitted(Message):
        """用户按 Enter 提交任务时触发。"""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class CompletionRequested(Message):
        pass

    def __init__(self) -> None:
        super().__init__(
            id="prompt-input",
            placeholder="Ask mini-openclaw...",
        )
        self._history: list[str] = []
        self._history_index: int = -1

    def action_submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        self._history.append(text)
        self._history_index = len(self._history)
        self.post_message(self.Submitted(text))
        self.clear()

    def action_complete(self) -> None:
        self.post_message(self.CompletionRequested())
