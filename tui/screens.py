"""主屏幕与权限弹窗。"""

from __future__ import annotations
import json
import asyncio

from textual.screen import Screen, ModalScreen
from textual.widgets import Button, Static, Label
from textual.containers import Vertical, Horizontal
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.binding import Binding
from textual.message import Message

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT as SYSTEM_PROMPT_SRC

from tui.status_bar import StatusBar, LOGO_ART
from tui.chat_view import ChatContainer, AssistantMessage
from tui.input_area import PromptInput
from tui.widgets import ToolCallCard
from tui.state import (
    ConversationState,
    TokenEvent,
    ToolCallStartEvent,
    ToolCallEndEvent,
    StatusEvent,
    ErrorEvent,
    DoneEvent,
    TokenUsageEvent,
)
from tui.backend import get_streaming_backend
from tui.worker import AgentWorker


class PermissionModal(ModalScreen[bool]):
    """权限确认弹窗。返回 True（同意）或 False（拒绝）。"""

    def __init__(self, tool_name: str, arguments: dict) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments

    DEFAULT_CSS = """
    PermissionModal {
        align: center middle;
    }
    #perm-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $warning;
    }
    #perm-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #perm-args {
        margin: 1 0;
    }
    #perm-warning {
        color: $error;
        margin: 1 0;
    }
    #perm-buttons {
        align: center middle;
        margin-top: 1;
    }
    #perm-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="perm-dialog"):
            yield Label("[bold]🔒 权限请求[/bold]", id="perm-title")
            yield Label(f"工具: [bold]{self.tool_name}[/bold]")
            args_str = json.dumps(self.arguments, indent=2, ensure_ascii=False)
            yield Static(f"参数:\n{args_str[:800]}", id="perm-args")
            if self.tool_name in ("bash", "write", "edit"):
                yield Label("⚠ 此操作可修改系统文件！", id="perm-warning")
            with Horizontal(id="perm-buttons"):
                yield Button("✅ 允许 (Y)", variant="primary", id="perm-yes")
                yield Button("❌ 拒绝 (N)", variant="error", id="perm-no")

    BINDINGS = [
        Binding("y", "approve", "允许"),
        Binding("n", "deny", "拒绝"),
        Binding("escape", "deny", "取消"),
    ]

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "perm-yes")


class MainScreen(Screen):
    """主对话屏幕。"""

    status = reactive("idle")
    turn_count = reactive(0)

    BINDINGS = [
        Binding("escape", "cancel", "取消操作"),
        Binding("ctrl+c", "quit", "退出"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._state = ConversationState()
        self._worker: AgentWorker | None = None
        self._event_poller: asyncio.Task | None = None
        self._current_assistant_msg: AssistantMessage | None = None
        self._tool_cards: dict[str, ToolCallCard] = {}
        self._backend = get_streaming_backend()
        self._registry = build_default_registry()

    def compose(self) -> ComposeResult:
        yield StatusBar()
        yield ChatContainer()
        yield PromptInput()

    def on_mount(self) -> None:
        """应用启动时显示欢迎消息与 logo。"""
        from tui.chat_view import MessageList
        welcome = (
            f"{LOGO_ART}\n\n"
            "欢迎使用 [bold cyan]mini-openclaw[/bold cyan]！\n\n"
            "这是一个基于 DeepSeek API 的 CLI 智能体。\n\n"
            "在下方输入你的任务描述，按 [bold]Enter[/bold] 提交，"
            "按 [bold]Shift+Enter[/bold] 换行，"
            "按 [bold]Escape[/bold] 取消当前操作。\n"
        )
        try:
            ml = self.query_one(MessageList)
            msg = AssistantMessage(welcome)
            ml.mount(msg)
        except Exception:
            pass

    def watch_status(self, old: str, new: str) -> None:
        """status 变化时自动更新状态栏。"""
        try:
            self.query_one(StatusBar).set_status(new)
        except Exception:
            pass
        if new in ("done", "error", "cancelled"):
            try:
                self.query_one(PromptInput).disabled = False
                self.query_one(PromptInput).focus()
            except Exception:
                pass

    def watch_turn_count(self, old: int, new: int) -> None:
        try:
            self.query_one(StatusBar).set_turn(new)
        except Exception:
            pass

    async def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        """用户提交任务后启动 Worker。"""
        # 添加用户消息
        await self.query_one(ChatContainer).add_user_message(event.text)
        self._state.add_user_message(event.text)

        # 创建助手消息占位
        self._current_assistant_msg = await self.query_one(ChatContainer).add_assistant_message()

        # 重置工具卡片跟踪
        self._tool_cards = {}

        # 创建并启动 Worker
        self._worker = AgentWorker(
            event.text,
            self._backend,
            self._registry,
            SYSTEM_PROMPT_SRC,
        )

        self.status = "thinking"
        self.turn_count = 0
        try:
            self.query_one(PromptInput).disabled = True
        except Exception:
            pass

        # 在独立线程中运行 Worker
        import threading
        self._worker_thread = threading.Thread(target=self._worker.run, daemon=True)
        self._worker_thread.start()
        self._event_poller = asyncio.create_task(self._poll_events())

    async def _poll_events(self) -> None:
        """轮询 Worker 事件队列并分发。"""
        queue = self._worker.get_event_queue()
        try:
            while True:
                event = await queue.get()
                await self._dispatch_event(event)
                if isinstance(event, (DoneEvent, ErrorEvent)):
                    break
        except asyncio.CancelledError:
            pass

    async def _dispatch_event(self, event: object) -> None:
        """分发事件到对应处理器。"""
        if isinstance(event, TokenEvent):
            if self._current_assistant_msg:
                await self._current_assistant_msg.append_token(event.text)

        elif isinstance(event, ToolCallStartEvent):
            self.status = "executing"
            card = ToolCallCard(event.name, event.arguments)
            # 设置 ID 以便后续查找
            card.id = f"tool-call-{event.call_id or id(card)}"
            self._tool_cards[event.call_id] = card
            if self._current_assistant_msg:
                await self._current_assistant_msg.mount(card)
            await self.query_one(ChatContainer).scroll_to_bottom()

        elif isinstance(event, ToolCallEndEvent):
            card = self._tool_cards.get(event.call_id)
            if card:
                await card.set_result(event.result, event.duration_ms)
            self.status = "thinking"

        elif isinstance(event, StatusEvent):
            self.status = event.status

        elif isinstance(event, TokenUsageEvent):
            try:
                self.query_one(StatusBar).set_token_usage(
                    event.prompt_tokens, event.completion_tokens
                )
            except Exception:
                pass

        elif isinstance(event, ErrorEvent):
            self.status = "error"
            if self._current_assistant_msg:
                await self._current_assistant_msg.append_token(
                    f"\n\n[red]❌ 错误：{event.message}[/red]"
                )

        elif isinstance(event, DoneEvent):
            self.status = "done"
            if self._current_assistant_msg:
                await self._current_assistant_msg.finalize_content()

    def action_cancel(self) -> None:
        """Escape 取消当前操作。"""
        if self._worker:
            self._worker.cancel()
            self.status = "cancelled"
            # 取消事件轮询
            if self._event_poller and not self._event_poller.done():
                self._event_poller.cancel()
                self._event_poller = None
            # 重新启用输入
            try:
                self.query_one(PromptInput).disabled = False
                self.query_one(PromptInput).focus()
            except Exception:
                pass
