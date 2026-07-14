"""Terminal-native previews for generated workspace artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Markdown, Static

from tui.file_link import resolve_artifact


IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt"}


def image_to_ansi(path: Path, max_width: int, max_height: int) -> Text:
    """Render an image with ANSI true-color half-block characters."""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")

    width_limit = max(1, max_width)
    pixel_height_limit = max(2, max_height * 2)
    scale = min(width_limit / image.width, pixel_height_limit / image.height, 1.0)
    size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)

    output = Text()
    for y in range(0, image.height, 2):
        bottom_y = min(y + 1, image.height - 1)
        for x in range(image.width):
            top = image.getpixel((x, y))
            bottom = image.getpixel((x, bottom_y))
            output.append(
                "▀",
                Style(
                    color=Color.from_rgb(*top),
                    bgcolor=Color.from_rgb(*bottom),
                ),
            )
        if y + 2 < image.height:
            output.append("\n")
    return output


class ArtifactPreviewModal(ModalScreen[None]):
    """Preview Markdown, text, images, and visual evidence inside the TUI."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("left", "previous_frame", "Previous frame"),
        Binding("right", "next_frame", "Next frame"),
    ]

    def __init__(self, path: Path, root: Path | None = None) -> None:
        super().__init__()
        self.path = path
        self.root = (root or Path.cwd()).resolve()
        self.records: list[dict[str, Any]] = []
        self.record_index = 0
        self.load_error = ""
        if path.name == "visual_notes.jsonl":
            self.records = self._load_visual_records()

    @classmethod
    def supports(cls, path: Path) -> bool:
        return path.suffix.lower() in IMAGE_SUFFIXES | TEXT_SUFFIXES

    @property
    def is_visual_notes(self) -> bool:
        return self.path.name == "visual_notes.jsonl"

    def compose(self) -> ComposeResult:
        with Vertical(id="artifact-preview-dialog"):
            with Horizontal(id="artifact-preview-header"):
                yield Label(self.path.name, id="artifact-preview-title")
                close = Button("X", id="artifact-preview-close")
                close.tooltip = "Close preview"
                yield close
            with VerticalScroll(id="artifact-preview-body"):
                if self.path.suffix.lower() == ".md":
                    yield Markdown(self._read_text(), id="artifact-preview-markdown")
                elif self.is_visual_notes:
                    yield Static("", id="artifact-preview-image", markup=False)
                    yield Static("", id="artifact-preview-details", markup=False)
                elif self.path.suffix.lower() in IMAGE_SUFFIXES:
                    yield Static("", id="artifact-preview-image", markup=False)
                else:
                    yield Static(self._read_text(), id="artifact-preview-text", markup=False)
            if self.is_visual_notes:
                with Horizontal(id="artifact-preview-actions"):
                    previous = Button("<", id="artifact-preview-previous")
                    previous.tooltip = "Previous frame"
                    yield previous
                    following = Button(">", id="artifact-preview-next")
                    following.tooltip = "Next frame"
                    yield following

    def on_mount(self) -> None:
        self._refresh_preview()

    def on_resize(self, _event: Resize) -> None:
        if self.path.suffix.lower() in IMAGE_SUFFIXES or self.is_visual_notes:
            self.call_after_refresh(self._refresh_preview)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_previous_frame(self) -> None:
        if self.records and self.record_index > 0:
            self.record_index -= 1
            self._refresh_preview()

    def action_next_frame(self) -> None:
        if self.records and self.record_index < len(self.records) - 1:
            self.record_index += 1
            self._refresh_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "artifact-preview-close":
            self.action_close()
        elif event.button.id == "artifact-preview-previous":
            self.action_previous_frame()
        elif event.button.id == "artifact-preview-next":
            self.action_next_frame()

    def _read_text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return f"Unable to read artifact: {exc}"

    def _load_visual_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            for line_number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(f"line {line_number} is not an object")
                frame = str(record.get("frame") or "")
                if frame:
                    try:
                        record["_frame_path"] = resolve_artifact(frame, self.root)
                    except (OSError, ValueError) as exc:
                        record["_frame_error"] = str(exc)
                records.append(record)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            self.load_error = f"Unable to read visual notes: {exc}"
            records.clear()
        return records

    def _refresh_preview(self) -> None:
        if self.path.suffix.lower() not in IMAGE_SUFFIXES and not self.is_visual_notes:
            return
        image_widget = self.query("#artifact-preview-image").first()

        image_path = self.path
        if self.is_visual_notes:
            if not self.records:
                image_widget.update(Text(self.load_error or "No visual records", style="#ff928b"))
                self._update_navigation()
                return
            record = self.records[self.record_index]
            image_path = record.get("_frame_path")
            self.query_one("#artifact-preview-details", Static).update(self._record_details(record))
            self.query_one("#artifact-preview-title", Label).update(
                f"{self.path.name}  {self.record_index + 1}/{len(self.records)}"
            )

        if not isinstance(image_path, Path):
            error = str(self.records[self.record_index].get("_frame_error") or "Frame is unavailable")
            image_widget.update(Text(error, style="#ff928b"))
            self._update_navigation()
            return

        max_width = min(120, max(8, self.size.width - 10))
        reserved_rows = 18 if self.is_visual_notes else 10
        max_height = max(4, self.size.height - reserved_rows)
        try:
            image_widget.update(image_to_ansi(image_path, max_width, max_height))
        except (OSError, ValueError) as exc:
            image_widget.update(Text(f"Unable to render image: {exc}", style="#ff928b"))
        self._update_navigation()

    def _record_details(self, record: dict[str, Any]) -> Text:
        details = Text()
        page = record.get("page") or "?"
        timestamp = str(record.get("time") or "--:--")
        details.append(f"P{page}  {timestamp}", style="bold #63d4ed")
        backend = str(record.get("backend") or "unknown")
        confidence = str(record.get("confidence") or "unknown")
        details.append(f"  backend: {backend}  confidence: {confidence}\n", style="dim")
        visible_text = str(record.get("visible_text") or "").strip()
        summary = str(record.get("summary") or "").strip()
        fallback = str(record.get("text") or "").strip()
        if visible_text:
            details.append("\n画面文字\n", style="bold #ff928b")
            details.append(visible_text)
        if summary:
            details.append("\n\n画面描述\n", style="bold #ff928b")
            details.append(summary)
        elif fallback and not visible_text:
            details.append("\n\n识别结果\n", style="bold #ff928b")
            details.append(fallback)
        return details

    def _update_navigation(self) -> None:
        if not self.is_visual_notes:
            return
        previous = self.query("#artifact-preview-previous").first()
        following = self.query("#artifact-preview-next").first()
        if previous is not None:
            previous.disabled = not self.records or self.record_index == 0
        if following is not None:
            following.disabled = not self.records or self.record_index >= len(self.records) - 1
