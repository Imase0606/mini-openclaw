"""工具调用卡片组件。"""

from __future__ import annotations
import json
import time
from textual.widgets import Collapsible, Static
from textual.containers import Vertical
from textual.app import ComposeResult

# 工具图标映射
TOOL_ICONS = {
    "read": "📖",
    "write": "✏️",
    "bash": "💻",
    "edit": "🔧",
    "grep": "🔍",
    "glob": "📂",
    "web_fetch": "🌐",
    "final_answer": "✅",
    "task_list": "📋",
    "arxiv_search": "📄",
    "pdf_reader": "📕",
    "plot": "📊",
}

STATUS_LABELS = {
    "pending": "⏺ 待执行",
    "running": "⏳ 执行中",
    "done": "✅ 完成",
    "error": "❌ 错误",
    "denied": "🚫 已拒绝",
}

STATUS_COLORS = {
    "pending": "dim",
    "running": "bold yellow",
    "done": "bold green",
    "error": "bold red",
    "denied": "dim red",
}


class ToolCallCard(Collapsible):
    """可折叠的工具调用卡片。

    显示工具名称、图标、参数、执行结果。
    """

    def __init__(self, name: str, arguments: dict) -> None:
        self.tool_name = name
        self.tool_arguments = arguments
        self._start_time: float = time.monotonic()
        self._duration_ms: float = 0.0
        self._result_text: str = ""
        self._status: str = "pending"
        super().__init__(collapsed=True, title=self._build_title())

    def _build_title(self) -> str:
        icon = TOOL_ICONS.get(self.tool_name, "🔨")
        label = STATUS_LABELS.get(self._status, self._status)
        duration = f" ({self._duration_ms:.0f}ms)" if self._duration_ms else ""
        return f"{icon} {self.tool_name} {label}{duration}"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"[bold]Tool:[/bold] {self.tool_name}")
            # 参数区
            with Collapsible(title="Arguments", collapsed=True):
                yield Static(self._format_args(self.tool_arguments))
            # 结果区
            with Collapsible(title="Result", collapsed=True):
                yield Static("(pending...)", id="result-content")

    def _format_args(self, args: dict) -> str:
        """格式化参数，长文本截断。"""
        fmt = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 500:
                fmt[k] = v[:500] + f"... [截断，共 {len(v)} 字符]"
            else:
                fmt[k] = v
        return json.dumps(fmt, indent=2, ensure_ascii=False)

    async def set_running(self) -> None:
        self._status = "running"
        self._start_time = time.monotonic()
        self.title = self._build_title()

    async def set_result(self, result: str, duration_ms: float) -> None:
        self._duration_ms = duration_ms
        self._result_text = result
        self._status = "done"
        self.title = self._build_title()
        try:
            rw = self.query_one("#result-content")
            truncated = result[:2000]
            if len(result) > 2000:
                truncated += f"\n[dim]... (共 {len(result)} 字符，已截断)[/dim]"
            rw.update(truncated)
        except Exception:
            pass

    async def set_error(self, error: str) -> None:
        self._status = "error"
        self._duration_ms = (time.monotonic() - self._start_time) * 1000
        self.title = self._build_title()
        try:
            rw = self.query_one("#result-content")
            rw.update(f"[red]{error}[/red]")
        except Exception:
            pass

    async def set_denied(self) -> None:
        self._status = "denied"
        self.title = self._build_title()
        try:
            rw = self.query_one("#result-content")
            rw.update("[dim]用户拒绝了此操作[/dim]")
        except Exception:
            pass
