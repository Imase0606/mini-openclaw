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
        self.status = "running"
        self.result = ""
        self.duration_ms = 0
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
        self.status = status
        self.result = result
        self.duration_ms = duration_ms
        mark = {
            "done": "[ok]",
            "denied": "[denied]",
            "error": "[error]",
            "retrying": "[retry]",
            "recovered": "[recovered]",
        }.get(status, "[ ]")
        for state in ("done", "denied", "error", "retrying", "recovered"):
            self.remove_class(f"tool-{state}")
        if status in {"done", "denied", "error", "retrying", "recovered"}:
            self.add_class(f"tool-{status}")
        if status in {"retrying", "recovered"}:
            self.collapsed = True
        visual_summary = self._visual_summary(result) if self.tool_name == "video_frame_ocr" else ""
        self.title = Content(
            f"{mark} {self.tool_name}{self._summary(self.arguments)}{visual_summary}  {duration_ms}ms"
        )
        self.query_one(".tool-result", Static).update(result[:3000])

    def recover(self) -> None:
        if self.status == "retrying":
            self.finish("recovered", self.result, self.duration_ms)

    @staticmethod
    def _visual_summary(result: str) -> str:
        start = result.find("{")
        end = result.rfind("}")
        if start < 0 or end < start:
            return ""
        try:
            payload = json.loads(result[start:end + 1])
        except json.JSONDecodeError:
            return ""
        return (
            f"  {payload.get('visual_status', '--')}"
            f"/{payload.get('visual_backend', '--')}"
            f" {int(payload.get('frames_sampled') or 0)}f"
            f" {int(payload.get('records') or 0)}r"
        )

    @staticmethod
    def _summary(arguments: dict) -> str:
        for key in ("path", "url", "source_url", "command"):
            if arguments.get(key):
                value = str(arguments[key]).replace("\n", " ")[:72]
                return f"  {value}"
        return ""
