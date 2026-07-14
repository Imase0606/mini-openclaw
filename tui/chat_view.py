"""Claude-style welcome and conversation widgets."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Markdown, Static
from textual.widget import Widget


KNOWLEDGE_TERMINAL_ASCII = r"""
   .-----------------.
  /  >_  VIDEO + KB   \
 |   [>]  ===  ::     |
  '-------. .---------'
          |_|
""".strip("\n")


def app_version() -> str:
    try:
        return version("mini-openclaw")
    except PackageNotFoundError:
        return "0.2.0"


class WelcomePanel(Vertical):
    def __init__(
        self,
        model: str,
        workdir: str,
        permission: str,
        recent_sessions: list[tuple[str, str]],
        release: str,
    ) -> None:
        super().__init__()
        self.model = model
        self.workdir = workdir
        self.permission = permission
        self.recent_sessions = recent_sessions
        self.release = release
        self.border_title = f" mini-openclaw v{release} "

    def compose(self) -> ComposeResult:
        with Horizontal(classes="welcome-columns"):
            with Vertical(classes="welcome-left"):
                yield Static("Welcome back!", classes="welcome-greeting")
                yield Static(KNOWLEDGE_TERMINAL_ASCII, classes="welcome-logo", markup=False)
                yield Static(self.model, id="welcome-model", markup=False)
                yield Static(self.workdir, id="welcome-workdir", markup=False)
                yield Static(f"permission: {self.permission}", id="welcome-permission", markup=False)
            with Vertical(classes="welcome-right"):
                yield Static("Quick start", classes="welcome-heading")
                yield Static(
                    "[bold]/help[/bold]       Browse commands and shortcuts\n"
                    "[bold]/resume[/bold]     Continue a previous session\n"
                    "[bold]/model[/bold]      Switch configured model",
                    classes="quick-start",
                )
                yield Static("Recent sessions", classes="welcome-heading recent-heading")
                yield Static(self._recent_text(), classes="recent-sessions")

    def _recent_text(self) -> Text:
        if not self.recent_sessions:
            return Text("No previous sessions in this workspace")
        output = Text()
        for index, (session_id, title) in enumerate(self.recent_sessions[:3]):
            if index:
                output.append("\n")
            output.append(session_id, style="bold")
            output.append(f"  {title[:44]}")
        return output

    def set_model(self, model: str) -> None:
        self.model = model
        self.query_one("#welcome-model", Static).update(model)

    def set_permission(self, permission: str) -> None:
        self.permission = permission
        self.query_one("#welcome-permission", Static).update(f"permission: {permission}")


class MessageList(Vertical):
    pass


class ChatContainer(ScrollableContainer):
    def __init__(self) -> None:
        super().__init__()
        self._follow_timer = None

    def compose(self) -> ComposeResult:
        yield MessageList()

    async def add_user_message(self, text: str) -> None:
        await self.query_one(MessageList).mount(UserMessage(text))
        self.follow_output()

    async def add_assistant_message(self) -> "AssistantMessage":
        message = AssistantMessage()
        await self.query_one(MessageList).mount(message)
        self.follow_output()
        return message

    async def add_system_message(self, text: str, variant: str = "info") -> None:
        await self.query_one(MessageList).mount(SystemMessage(text, variant))
        self.follow_output()

    async def add_welcome(
        self,
        model: str,
        workdir: str,
        permission: str = "default",
        recent_sessions: list[tuple[str, str]] | None = None,
        release: str | None = None,
    ) -> None:
        await self.query_one(MessageList).mount(
            WelcomePanel(model, workdir, permission, recent_sessions or [], release or app_version())
        )
        self.follow_output()

    async def clear_messages(self) -> None:
        await self.query_one(MessageList).remove_children()

    def follow_output(self) -> None:
        self.scroll_end(animate=False, force=True, immediate=True)
        if self._follow_timer is not None:
            self._follow_timer.stop()
        self._follow_timer = self.set_timer(0.05, self._finish_follow)

    def _finish_follow(self) -> None:
        self._follow_timer = None
        self.scroll_end(animate=False, force=True, immediate=True)


class UserMessage(Static):
    def __init__(self, text: str) -> None:
        content = Text()
        content.append(">", style="bold #ff928b")
        content.append(f" {text}")
        super().__init__(content)


class SystemMessage(Static):
    MARKERS = {"info": "i", "success": "ok", "warning": "!", "error": "!", "denied": "x"}

    def __init__(self, text: str, variant: str = "info") -> None:
        safe_variant = variant if variant in self.MARKERS else "info"
        super().__init__(
            f"[{self.MARKERS[safe_variant]}] {text}",
            classes=f"notice-{safe_variant}",
            markup=False,
        )


class ActivityLine(Static):
    """Animated status bar shown at the bottom of each assistant message.

    Displays the current agent activity state (thinking, outputting, running a tool,
    etc.) with an animated frame, elapsed time, and interrupt hint.
    """

    STATUS_LABELS: dict[str, str] = {
        "thinking": "Thinking",
        "outputting": "Outputting",
        "running tool": "Running Tool",
        "waiting for permission": "Waiting for Permission",
        "planning": "Planning",
        "searching": "Searching",
        "reading": "Reading",
        "writing": "Writing",
        "compacting": "Compacting",
        "interrupting": "Interrupting",
        "analyzing": "Analyzing",
        "generating": "Generating",
        "summarizing": "Summarizing",
        "reviewing": "Reviewing",
        "coding": "Coding",
        "debugging": "Debugging",
        "testing": "Testing",
        "optimizing": "Optimizing",
        "fetching": "Fetching",
        "installing": "Installing",
        "completed": "",
        "idle": "",
    }

    FRAMES = (".  ", ".. ", "...")

    def __init__(self) -> None:
        super().__init__("", classes="activity-line")
        self.status = ""
        self.tool = ""
        self.turn = 0
        self.frame = 0
        self.started_at = time.monotonic()
        self.display = False

    def on_mount(self) -> None:
        self.set_interval(0.35, self._tick)

    def set_activity(self, status: str, tool: str = "", turn: int = 0) -> None:
        next_turn = turn or self.turn
        if (status, tool, next_turn) != (self.status, self.tool, self.turn):
            self.started_at = time.monotonic()
        self.status = status
        self.tool = tool
        self.turn = next_turn
        self.display = bool(status and status not in {"idle", "completed"})
        self._render_activity()

    def clear(self) -> None:
        self.status = ""
        self.display = False
        self.update("")

    def _tick(self) -> None:
        if not self.display:
            return
        self.frame = (self.frame + 1) % len(self.FRAMES)
        self._render_activity()

    def _get_display_label(self) -> str:
        """Return a human-readable label for the current status with ``...`` suffix."""
        raw = self.status.replace("_", " ").lower().strip()
        label = self.STATUS_LABELS.get(raw, raw.title())
        if not label:
            return ""
        return f"{label}..."

    def _render_activity(self) -> None:
        if not self.display:
            return
        label = self._get_display_label()
        if not label:
            self.update("")
            return

        detail = f"  {self.tool}" if self.tool else ""
        turn = f"  turn {self.turn}" if self.turn else ""
        elapsed = max(0, int(time.monotonic() - self.started_at))

        content = Text()
        content.append(self.FRAMES[self.frame], style="bold #ff928b")
        content.append(f" {label}{detail}{turn}  {elapsed}s  ")
        content.append("ctrl+c to interrupt", style="dim")
        self.update(content)


class AssistantMessage(Vertical):
    """A single assistant response, composed of a Markdown body, optional tool
    call cards, copy actions, and an ActivityLine pinned at the bottom."""

    def __init__(self) -> None:
        super().__init__()
        self.content = ""
        self._flush_scheduled = False
        self._has_tool_output = False

    def compose(self) -> ComposeResult:
        yield Markdown("")
        actions = Horizontal(classes="assistant-actions")
        actions.display = False
        with actions:
            copy = Button("Copy", classes="copy-response")
            copy.tooltip = "Copy response"
            yield copy
        yield ActivityLine()

    async def mount_tool_card(self, card: Widget) -> None:
        """Mount tool output before message actions and the activity line."""
        actions = self.query_one(".assistant-actions")
        await self.mount(card, before=actions)

    async def append_token(self, text: str) -> None:
        self.content += text
        if not self._flush_scheduled:
            self._flush_scheduled = True
            self.set_timer(0.04, self._flush)

    def _flush(self) -> None:
        self._flush_scheduled = False
        if not self.is_mounted:
            return
        markdown = self.query(Markdown).first()
        if markdown is not None:
            markdown.update(self.content)

    async def finalize(self) -> None:
        self._flush()
        self.clear_activity()
        self.query_one(".assistant-actions").display = bool(self.content.strip())

    def copy_content(self) -> bool:
        if not self.content.strip():
            return False
        self.app.copy_to_clipboard(self.content)
        self.notify("Copied response")
        return True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("copy-response"):
            self.copy_content()

    @property
    def has_visible_content(self) -> bool:
        return bool(self.content.strip() or self._has_tool_output)

    def mark_tool_output(self) -> None:
        self._has_tool_output = True

    async def discard_if_empty(self) -> bool:
        if self.has_visible_content or not self.is_mounted:
            return False
        await self.remove()
        return True

    def set_activity(self, status: str, tool: str = "", turn: int = 0) -> None:
        self.query_one(ActivityLine).set_activity(status, tool, turn)

    def clear_activity(self) -> None:
        if self.is_mounted:
            self.query_one(ActivityLine).clear()
