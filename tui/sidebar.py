"""Todo, artifact and turn-setting side panel."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class SidePanel(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Todo", classes="side-title")
        yield Static("No active plan", id="todo-content", markup=False)
        yield Static("Artifacts", classes="side-title")
        yield Static("No artifacts", id="artifact-content", markup=False)
        yield Static("Turn settings", classes="side-title")
        yield Static("plan: auto\npermission: default", id="setting-content", markup=False)
        yield Static("Trace", classes="side-title")
        yield Static("Trace unavailable", id="trace-content", markup=False)

    def set_todo(self, rendered: str) -> None:
        self.query_one("#todo-content", Static).update(rendered or "No active plan")

    def set_artifacts(self, paths: list[str]) -> None:
        text = "\n".join(f"{index}. {path}" for index, path in enumerate(paths, 1))
        self.query_one("#artifact-content", Static).update(text or "No artifacts")

    def set_settings(self, planning: str, video_type: str, permission: str, images: int) -> None:
        self.query_one("#setting-content", Static).update(
            f"plan: {planning}\nvideo: {video_type}\npermission: {permission}\nimages: {images}"
        )

    def set_trace(self, path: str, tokens: int = 0) -> None:
        self.query_one("#trace-content", Static).update(f"{path}\ntokens: {tokens}" if path else "Trace unavailable")
