"""Tool execution and run-detail widgets."""
from __future__ import annotations

import json

from textual.content import Content
from textual.containers import Vertical
from textual.widgets import Collapsible, Static


class ToolCallCard(Collapsible):
    def __init__(self, call_id: str, name: str, arguments: dict) -> None:
        self.call_id = call_id
        self.tool_name = name
        self.arguments = arguments
        details = Vertical(
            Static(
                json.dumps(self.arguments, ensure_ascii=False, indent=2),
                classes="tool-arguments",
                markup=False,
            ),
            Static("Running...", classes="tool-result", markup=False),
            classes="tool-details",
        )
        # Keep details inside Collapsible.Contents so collapsed cards stay compact.
        super().__init__(
            details,
            title=Content(f"[*] {name}{self._summary(arguments)}"),
            collapsed=True,
        )

    def finish(self, status: str, result: str, duration_ms: int) -> None:
        mark = {"done": "[ok]", "denied": "[denied]", "error": "[error]"}.get(status, "[ ]")
        for state in ("done", "denied", "error"):
            self.remove_class(f"tool-{state}")
        if status in {"done", "denied", "error"}:
            self.add_class(f"tool-{status}")
        self.title = Content(f"{mark} {self.tool_name}{self._summary(self.arguments)}  {duration_ms}ms")
        self.query_one(".tool-result", Static).update(result[:3000])

    @staticmethod
    def _summary(arguments: dict) -> str:
        for key in ("path", "url", "source_url", "command"):
            if arguments.get(key):
                value = str(arguments[key]).replace("\n", " ")[:72]
                return f"  {value}"
        return ""
