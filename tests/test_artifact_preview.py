from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from textual.widgets import Markdown, Static

from agent.runtime import AgentRuntime
from backend.fake_backend import FakeBackend
from tui.app import MiniOpenClawApp
from tui.artifact_preview import ArtifactPreviewModal, image_to_ansi
from tui.file_link import resolve_artifact
from tui.screens import MainScreen
from tui.state import ArtifactRecord


class ArtifactPreviewTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def runtime_factory() -> AgentRuntime:
        return AgentRuntime(backend=FakeBackend(), trace_enabled=False, enable_mcp=False)

    def test_resolve_artifact_rejects_paths_outside_workspace(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            inside = Path(workspace) / "inside.md"
            inside.write_text("# inside", encoding="utf-8")
            external = Path(outside) / "outside.md"
            external.write_text("# outside", encoding="utf-8")

            self.assertEqual(resolve_artifact("inside.md", Path(workspace)), inside.resolve())
            with self.assertRaisesRegex(ValueError, "outside the workspace"):
                resolve_artifact(str(external), Path(workspace))

    def test_image_to_ansi_respects_terminal_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wide.png"
            Image.new("RGB", (100, 40), (240, 20, 10)).save(path)
            rendered = image_to_ansi(path, max_width=20, max_height=4)

            lines = rendered.plain.splitlines()
            self.assertLessEqual(max(map(len, lines)), 20)
            self.assertLessEqual(len(lines), 4)
            self.assertTrue(rendered.spans)

    async def test_open_markdown_uses_internal_modal(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            path = Path(tmp) / "index.md"
            path.write_text("# 中文标题\n\n- 内容", encoding="utf-8")
            relative = path.relative_to(Path.cwd()).as_posix()
            app = MiniOpenClawApp(self.runtime_factory)
            async with app.run_test(size=(80, 30)) as pilot:
                await pilot.pause()
                screen = app.screen
                self.assertIsInstance(screen, MainScreen)
                screen.artifacts = [ArtifactRecord("markdown_path", relative)]

                await screen._open_artifact(["1"])
                await pilot.pause()

                self.assertIsInstance(app.screen, ArtifactPreviewModal)
                markdown = app.screen.query_one("#artifact-preview-markdown", Markdown)
                self.assertIn("中文标题", markdown._markdown)
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsInstance(app.screen, MainScreen)

    async def test_visual_notes_preview_navigates_frames_on_narrow_terminal(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            frames = root / "assets" / "frames" / "p1"
            frames.mkdir(parents=True)
            records = []
            for index, color in enumerate(("red", "blue"), 1):
                frame = frames / f"frame_{index:03d}.jpg"
                Image.new("RGB", (80, 40), color).save(frame)
                records.append({
                    "page": 1,
                    "time": f"00:0{index}",
                    "frame": frame.relative_to(Path.cwd()).as_posix(),
                    "visible_text": f"文字 {index}",
                    "summary": f"描述 {index}",
                    "confidence": "high",
                    "backend": "mimo",
                })
            notes = root / "visual_notes.jsonl"
            notes.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            app = MiniOpenClawApp(self.runtime_factory)
            async with app.run_test(size=(60, 30)) as pilot:
                await pilot.pause()
                app.push_screen(ArtifactPreviewModal(notes.resolve()))
                await pilot.pause()

                modal = app.screen
                self.assertIsInstance(modal, ArtifactPreviewModal)
                dialog = modal.query_one("#artifact-preview-dialog")
                self.assertLessEqual(dialog.region.width, 60)
                details = modal.query_one("#artifact-preview-details", Static)
                self.assertIn("文字 1", str(details.render()))

                await pilot.press("right")
                await pilot.pause()
                self.assertEqual(modal.record_index, 1)
                self.assertIn("文字 2", str(details.render()))
