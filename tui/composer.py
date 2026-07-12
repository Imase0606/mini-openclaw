"""Claude-style prompt composer and compact session status."""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from tui.input_area import PromptInput


class Composer(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(id="composer-input-row"):
            yield Static(">", id="prompt-mark", markup=False)
            yield PromptInput()
        yield Static("", id="attachment-line", markup=False)
        with Horizontal(id="composer-footer"):
            yield Static("default  |  deepseek  |  ctx 0%", id="composer-status", markup=False)
            yield Static("shift+tab: mode", id="composer-hint", markup=False)

    def set_state(
        self,
        *,
        permission: str,
        model: str,
        context_percent: float,
        images: list[str],
        queued: int,
        busy: bool,
    ) -> None:
        for mode in ("default", "accept-edits", "plan"):
            self.remove_class(f"permission-{mode}")
        css_mode = "accept-edits" if permission == "acceptEdits" else permission
        self.add_class(f"permission-{css_mode}")

        details = [permission, model, f"ctx {context_percent:.0f}%"]
        if images:
            details.append(f"{len(images)} image{'s' if len(images) != 1 else ''}")
        if queued:
            details.append(f"{queued} queued")
        self.query_one("#composer-status", Static).update("  |  ".join(details))
        self.query_one("#composer-hint", Static).update(
            "ctrl+c: interrupt" if busy else "shift+tab: mode  ctrl+b: details"
        )

        attachment = self.query_one("#attachment-line", Static)
        attachment.update("attachments  " + "  ".join(Path(path).name for path in images) if images else "")
        attachment.display = bool(images)
