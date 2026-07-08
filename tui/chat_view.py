"""聊天区域：可滚动的对话视图 + 用户/助手消息组件。"""

from __future__ import annotations
from textual.widgets import Static, RichLog, Markdown
from textual.containers import ScrollableContainer, Vertical
from textual.app import ComposeResult
from rich.markup import escape as rich_escape


class MessageList(Vertical):
    """消息列表容器，自动纵向扩展。"""
    pass


class ChatContainer(ScrollableContainer):
    """可滚动的对话区域。"""

    BORDER_TITLE = "对话"

    def compose(self) -> ComposeResult:
        yield MessageList()

    async def add_user_message(self, text: str) -> None:
        msg = UserMessage(text)
        await self.query_one(MessageList).mount(msg)
        self.scroll_end(animate=False)

    async def add_assistant_message(self, text: str = "") -> AssistantMessage:
        msg = AssistantMessage(text)
        await self.query_one(MessageList).mount(msg)
        self.scroll_end(animate=False)
        return msg

    async def scroll_to_bottom(self) -> None:
        self.scroll_end(animate=False)


class UserMessage(Static):
    """用户消息，以 Markdown 渲染。"""

    def __init__(self, text: str) -> None:
        super().__init__(f"> **你：** {text}")


class AssistantMessage(Vertical):
    """助手消息，支持流式追加 token 和内嵌工具调用卡片。"""

    def __init__(self, initial_text: str = "") -> None:
        super().__init__()
        self._accumulated = initial_text
        self._tool_widgets: list = []

    def compose(self) -> ComposeResult:
        """初始只放一个 RichLog 用于流式显示。"""
        self._log = RichLog(highlight=True, markup=True, max_lines=10000)
        yield self._log
        if self._accumulated:
            self._log.write(self._accumulated)

    async def append_token(self, token: str) -> None:
        """追加一个流式 token 到 RichLog。"""
        self._accumulated += token
        try:
            # 转义方括号等 Rich 标记字符，防止意外渲染
            safe = rich_escape(token)
            self._log.write(safe)
        except Exception:
            pass  # 容错：某些字符可能导致 Rich 渲染异常

    async def finalize_content(self) -> None:
        """流式完成后，尝试将内容完整渲染。"""
        if not self._accumulated:
            return
        # 在 RichLog 下方追加一个 Markdown 组件以完整渲染
        # （不移除 RichLog，保持流式显示的历史感）
        try:
            md = Markdown(self._accumulated)
            self._md_widget = md
            await self.mount(md)
        except Exception:
            pass

    def set_content_direct(self, text: str) -> None:
        """非流式模式：直接设置内容。"""
        self._accumulated = text
        try:
            self._log.clear()
            self._log.write(text)
        except Exception:
            pass
