from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from agent.events import AgentEvent
from agent.loop import AgentLoop
from agent.policy import ToolPolicy
from agent.runtime import AgentRuntime
from backend.client import DeepSeekBackend
from backend.fake_backend import FakeBackend
from tools.base import ToolRegistry
from tui.app import MiniOpenClawApp
from tui.chat_view import ChatContainer
from tui.screens import MainScreen, PermissionModal
from tui.sidebar import SidePanel
from tui.widgets import ToolCallCard


class RecordingBackend:
    def __init__(self):
        self.requests = []

    def chat(self, messages, tools=None):
        self.requests.append(messages)
        return {
            "role": "assistant",
            "content": f"answer-{len(self.requests)}",
            "tool_calls": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }


class PermissionBackend:
    model = "permission-test"

    def __init__(self, path: str):
        self.path = path
        self.step = 0

    def chat_stream(self, messages, tools=None):
        self.step += 1
        if self.step == 1:
            yield {
                "type": "tool_call",
                "id": "write-1",
                "name": "write",
                "arguments": {"path": self.path, "content": "blocked"},
            }
        else:
            yield {"type": "content", "delta": "操作已拒绝。"}
        yield {"type": "usage", "prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
        yield {"type": "done"}


class StreamResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return iter([
            'data: {"choices":[{"delta":{"content":"hello "},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read","arguments":"{\\"path\\":\\"a"}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":".txt\\"}"}}]},"finish_reason":"tool_calls"}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}',
            "data: [DONE]",
        ])


class StreamClient:
    def stream(self, *_args, **_kwargs):
        return StreamResponse()


class AgentEventTests(unittest.TestCase):
    def test_run_turn_preserves_history_and_run_stays_compatible(self):
        backend = RecordingBackend()
        loop = AgentLoop(backend, ToolRegistry(), "system", tool_policy=ToolPolicy())
        first = loop.run_turn("first")
        second = loop.run_turn("second", history=first.messages)
        self.assertEqual(first.content, "answer-1")
        self.assertEqual(second.content, "answer-2")
        self.assertTrue(any(message.get("content") == "first" for message in backend.requests[1]))
        self.assertEqual(AgentLoop(RecordingBackend(), ToolRegistry(), "system").run("x"), "answer-1")

    def test_stream_events_and_cancellation(self):
        events: list[AgentEvent] = []
        loop = AgentLoop(
            FakeBackend(),
            ToolRegistry(),
            "system",
            event_sink=events.append,
            tool_policy=ToolPolicy(),
        )
        result = loop.run_turn("hello")
        self.assertEqual(result.status, "completed")
        self.assertIn("text_delta", [event.kind for event in events])
        self.assertIn("run_finished", [event.kind for event in events])

        cancel = threading.Event()
        cancel.set()
        cancelled = AgentLoop(
            FakeBackend(), ToolRegistry(), "system", cancel_event=cancel
        ).run_turn("hello")
        self.assertEqual(cancelled.status, "cancelled")

    def test_backend_stream_accumulates_fragmented_tool_calls_and_usage(self):
        backend = object.__new__(DeepSeekBackend)
        backend.api_key = "test"
        backend.base_url = "https://example.com"
        backend.model = "stream-test"
        backend._client = StreamClient()
        events = list(backend.chat_stream([{"role": "user", "content": "hi"}], []))
        self.assertEqual(events[0], {"type": "content", "delta": "hello "})
        tool = next(event for event in events if event["type"] == "tool_call")
        self.assertEqual(tool["name"], "read")
        self.assertEqual(tool["arguments"], {"path": "a.txt"})
        usage = next(event for event in events if event["type"] == "usage")
        self.assertEqual(usage["total_tokens"], 6)


class TUITests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def runtime_factory() -> AgentRuntime:
        return AgentRuntime(backend=FakeBackend(), trace_enabled=False, enable_mcp=False)

    async def test_commands_and_multiturn_submission(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            self.assertIsInstance(screen, MainScreen)
            prompt = screen.query_one("#prompt-input")
            prompt.text = "/plan on"
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(screen.settings.planning_mode, "force")

            prompt.text = "hello"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause(0.05)
                if not screen.busy:
                    break
            self.assertFalse(screen.busy)
            self.assertTrue(screen.runtime and screen.runtime.history)
            sidebar = screen.query_one(SidePanel)
            chat = screen.query_one(ChatContainer)
            screen.action_toggle_drawer()
            await pilot.pause()
            self.assertLessEqual(chat.region.right, sidebar.region.x)

            prompt.text = "/clear"
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(screen.runtime.history, [])

    async def test_image_command_and_narrow_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "sample.png"
            Image.new("RGB", (16, 16), "red").save(image_path)
            app = MiniOpenClawApp(self.runtime_factory)
            async with app.run_test(size=(80, 32)) as pilot:
                await pilot.pause()
                screen = app.screen
                prompt = screen.query_one("#prompt-input")
                prompt.text = f'/image "{image_path}"'
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(screen.pending_images, [str(image_path)])
                self.assertTrue(screen.has_class("narrow"))
                screen.action_toggle_drawer()
                await pilot.pause()
                self.assertGreater(screen.query_one(SidePanel).region.height, 0)
                chat = screen.query_one(ChatContainer)
                sidebar = screen.query_one(SidePanel)
                self.assertLessEqual(chat.region.bottom, sidebar.region.y)
                screenshot = app.save_screenshot(filename="tui-narrow.svg", path=tmp)
                self.assertGreater(Path(screenshot).stat().st_size, 1000)

    async def test_permission_modal_denies_write_and_updates_tool_card(self):
        target = Path("tui-permission-denied.tmp")
        target.unlink(missing_ok=True)
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.runtime is not None
            screen.runtime.text_backend = PermissionBackend(str(target))
            prompt = screen.query_one("#prompt-input")
            prompt.text = "write a file"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause(0.05)
                if isinstance(app.screen, PermissionModal):
                    break
            self.assertIsInstance(app.screen, PermissionModal)
            await pilot.press("n")
            for _ in range(40):
                await pilot.pause(0.05)
                if not screen.busy:
                    break
            self.assertFalse(target.exists())
            card = screen.query_one(ToolCallCard)
            self.assertIn("[denied]", str(card.title))


if __name__ == "__main__":
    unittest.main()
