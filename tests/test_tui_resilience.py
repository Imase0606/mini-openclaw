from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.widgets import Static

from agent.context import strip_transient_planning_history
from agent.events import AgentEvent, RunResult
from agent.loop import AgentLoop
from agent.policy import ToolPolicy
from agent.runtime import AgentRuntime, RuntimeOptions
from tools.base import ToolRegistry
from tools import video
from tui.app import MiniOpenClawApp
from tui.chat_view import AssistantMessage, ChatContainer
from tui.screens import MainScreen
from tui.state import QueuedRequest, TUISettings
from tui.widgets import ToolCallCard
from tui.worker import AgentWorker


MALFORMED_MARKUP = '{\n  "value": "[broken=\\\"\",\n",\n  "items": ["[bold]", "[/missing]"]\n}'


class SequenceBackend:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.requests.append(messages)
        return {"role": "assistant", "usage": {}, **self.responses.pop(0)}


class EmptyStreamBackend:
    model = "empty-stream"

    def __init__(self) -> None:
        self.calls = 0

    def chat_stream(self, messages, tools=None):
        self.calls += 1
        yield {"type": "usage", "prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}
        yield {"type": "done"}


class LongStreamBackend:
    model = "long-stream"

    def chat_stream(self, messages, tools=None):
        for index in range(50):
            yield {"type": "content", "delta": f"line {index}\n"}
        yield {"type": "done"}


class StubRuntime:
    def __init__(self, mode: str = "normal") -> None:
        self.mode = mode
        self.event_sink = None
        self.confirm_callback = None

    def run_turn(self, task, *, options, cancel_event):
        if self.mode == "error":
            raise RuntimeError("backend failed")
        if self.mode == "cancel":
            while not cancel_event.wait(0.01):
                pass
            return RunResult("cancelled", [], "cancelled", 0)
        return RunResult("done", [], "completed", 1)

    def execute_direct_tool(self, name, arguments, *, permission_mode):
        return "direct done"


class ControlledWorker:
    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.cancelled = False
        self.permissions: list[tuple[str, bool]] = []

    def cancel(self) -> None:
        self.cancelled = True

    def resolve_permission(self, request_id: str, approved: bool) -> None:
        self.permissions.append((request_id, approved))


class AgentResilienceTests(unittest.TestCase):
    def test_empty_response_retries_once_then_succeeds(self):
        backend = SequenceBackend([
            {"content": "", "tool_calls": []},
            {"content": "recovered", "tool_calls": []},
        ])
        result = AgentLoop(backend, ToolRegistry(), "system", tool_policy=ToolPolicy()).run_turn("task")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.content, "recovered")
        self.assertEqual(len(backend.requests), 2)
        self.assertTrue(any("响应为空" in str(item.get("content")) for item in backend.requests[1]))

    def test_two_empty_responses_stop_with_explicit_status(self):
        events: list[AgentEvent] = []
        backend = SequenceBackend([
            {"content": "", "tool_calls": []},
            {"content": "", "tool_calls": []},
        ])
        result = AgentLoop(
            backend,
            ToolRegistry(),
            "system",
            tool_policy=ToolPolicy(),
            event_sink=events.append,
        ).run_turn("task")
        self.assertEqual(result.status, "empty_response")
        self.assertIn("空响应", result.content)
        terminal = [event for event in events if event.kind == "run_finished"]
        self.assertEqual(terminal[-1].data["status"], "empty_response")

    def test_planning_transactions_are_removed_but_business_calls_remain(self):
        history = [
            {"role": "user", "content": "old task"},
            {
                "role": "assistant",
                "content": "working",
                "tool_calls": [
                    {"id": "todo-1", "name": "update_todo", "arguments": {"id": 2}},
                    {"id": "read-1", "name": "read", "arguments": {"path": "transcript.txt"}},
                ],
            },
            {"role": "tool", "name": "update_todo", "tool_call_id": "todo-1", "content": "old todo"},
            {"role": "tool", "name": "read", "tool_call_id": "read-1", "content": "video data"},
        ]
        cleaned = strip_transient_planning_history(history)
        text = str(cleaned)
        self.assertNotIn("update_todo", text)
        self.assertNotIn("old todo", text)
        self.assertIn("transcript.txt", text)
        self.assertIn("video data", text)

    def test_runtime_does_not_send_old_todo_calls_to_next_turn(self):
        backend = SequenceBackend([{"content": "done", "tool_calls": []}])
        runtime = AgentRuntime(trace_enabled=False, enable_mcp=False)
        runtime.text_backend = backend
        runtime.history = [
            {"role": "user", "content": "previous"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "todo-old", "name": "update_todo", "arguments": {"id": 2}}],
            },
            {"role": "tool", "name": "update_todo", "tool_call_id": "todo-old", "content": "missing"},
            {"role": "assistant", "content": "knowledge_base/BV1TEST/index.md", "tool_calls": []},
        ]
        runtime.run_turn("new task", options=RuntimeOptions(planning_mode="off"))
        request_text = str([item for item in backend.requests[0] if item.get("role") != "system"])
        self.assertNotIn("update_todo", request_text)
        self.assertIn("knowledge_base/BV1TEST/index.md", request_text)
        self.assertNotIn("update_todo", str(runtime.history))
        runtime.close()


class VideoCacheTests(unittest.TestCase):
    def test_probe_reports_only_complete_existing_knowledge_base(self):
        metadata = {"bvid": "BV1CACHE", "title": "cached", "source_url": "https://www.bilibili.com/video/BV1CACHE/"}
        with tempfile.TemporaryDirectory() as tmp, patch.object(video, "KB_ROOT", Path(tmp)), patch.object(
            video, "_metadata_from_bili_api", return_value=metadata
        ):
            job = Path(tmp) / "BV1CACHE"
            job.mkdir()
            for name in ("index.md", "transcript.txt", "chunks.jsonl"):
                job.joinpath(name).write_text("ready", encoding="utf-8")
            ready = json.loads(video._probe(metadata["source_url"]))
            self.assertTrue(ready["knowledge_base_ready"])
            self.assertEqual(ready["index_path"], str(job / "index.md"))

            job.joinpath("chunks.jsonl").write_text("", encoding="utf-8")
            incomplete = json.loads(video._probe(metadata["source_url"]))
            self.assertFalse(incomplete["knowledge_base_ready"])
            self.assertEqual(incomplete["index_path"], "")


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def _run_worker(self, runtime: StubRuntime, *, direct: bool = False, cancel: bool = False):
        worker = AgentWorker(
            runtime,  # type: ignore[arg-type]
            "task",
            RuntimeOptions(),
            direct_tool=("bash", {"command": "echo ok"}) if direct else None,
        )
        thread = threading.Thread(target=worker.run)
        thread.start()
        if cancel:
            worker.cancel()
        events: list[AgentEvent] = []
        while True:
            event = await asyncio.wait_for(worker.queue.get(), 2)
            if isinstance(event, AgentEvent):
                events.append(event)
                if event.kind == "worker_finished":
                    break
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        return events

    async def test_worker_finished_is_emitted_once_for_all_exit_paths(self):
        cases = [
            (StubRuntime("normal"), False, False, "completed"),
            (StubRuntime("error"), False, False, "error"),
            (StubRuntime("normal"), True, False, "completed"),
            (StubRuntime("cancel"), False, True, "cancelled"),
        ]
        for runtime, direct, cancel, expected in cases:
            events = await self._run_worker(runtime, direct=direct, cancel=cancel)
            finished = [event for event in events if event.kind == "worker_finished"]
            self.assertEqual(len(finished), 1)
            self.assertEqual(finished[0].data["status"], expected)


class TUIResilienceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def runtime_factory() -> AgentRuntime:
        return AgentRuntime(trace_enabled=False, enable_mcp=False)

    async def test_malformed_tool_output_is_rendered_as_plain_text(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chat = app.screen.query_one(ChatContainer)
            await chat.add_user_message(MALFORMED_MARKUP)
            await chat.add_system_message(MALFORMED_MARKUP, "error")
            message = await chat.add_assistant_message()
            message.set_activity("running tool", MALFORMED_MARKUP, 1)
            card = ToolCallCard("bad-markup", "read", {"path": "[broken=\"value"})
            message.mark_tool_output()
            await message.mount(card)
            card.finish("done", MALFORMED_MARKUP, 12)
            await pilot.pause()
            self.assertTrue(card.collapsed)
            self.assertLessEqual(card.region.height, 3)
            rendered = str(card.query_one(".tool-result", Static).render())
            self.assertIn("[broken", rendered)
            self.assertIn("[/missing]", rendered)

    async def test_ui_failure_cancels_worker_clears_queue_and_waits_for_done(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            self.assertIsInstance(screen, MainScreen)
            worker = ControlledWorker()
            screen.worker = worker  # type: ignore[assignment]
            screen.busy = True
            screen.request_queue.append(QueuedRequest("agent", "later", (), TUISettings()))
            screen._dispatch_agent_event = AsyncMock(side_effect=RuntimeError("render failed"))  # type: ignore[method-assign]
            poller = asyncio.create_task(screen._poll_events(worker))  # type: ignore[arg-type]
            await worker.queue.put(AgentEvent("tool_finished", {"result": MALFORMED_MARKUP}))
            await pilot.pause(0.1)
            self.assertTrue(worker.cancelled)
            self.assertTrue(screen.busy)
            self.assertEqual(len(screen.request_queue), 0)
            self.assertFalse(poller.done())
            await worker.queue.put(AgentEvent("worker_finished", {"status": "cancelled"}))
            await asyncio.wait_for(poller, 2)
            self.assertFalse(screen.busy)
            self.assertIsNone(poller.exception())

    async def test_long_stream_keeps_final_output_in_view(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.runtime is not None
            screen.runtime.text_backend = LongStreamBackend()
            prompt = screen.query_one("#prompt-input")
            prompt.text = "stream many lines"
            await pilot.press("enter")
            for _ in range(80):
                await pilot.pause(0.05)
                if not screen.busy:
                    break
            chat = screen.query_one(ChatContainer)
            await pilot.pause()
            self.assertFalse(screen.busy)
            self.assertTrue(
                chat.is_vertical_scroll_end,
                f"scroll_y={chat.scroll_y} max_scroll_y={chat.max_scroll_y}",
            )

    async def test_two_empty_stream_responses_render_one_explicit_message(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.runtime is not None
            backend = EmptyStreamBackend()
            screen.runtime.text_backend = backend
            prompt = screen.query_one("#prompt-input")
            prompt.text = "return nothing"
            await pilot.press("enter")
            for _ in range(80):
                await pilot.pause(0.05)
                if not screen.busy:
                    break
            messages = list(screen.query(AssistantMessage))
            self.assertEqual(backend.calls, 2)
            self.assertEqual(len(messages), 1)
            self.assertIn("空响应", messages[0].content)
            self.assertFalse(screen.busy)


if __name__ == "__main__":
    unittest.main()
