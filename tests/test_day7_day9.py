from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agent.loop import AgentLoop
from agent.memory import KVMemory, Memory
from agent.planning import TodoList
from agent.policy import ToolPolicy
from agent.tracer import Tracer, cost_report, replay
from backend.client import DeepSeekBackend
from eval.planning_ablation import classify_outcome, render_report, successful_cached_run
from tools.base import Tool, ToolRegistry
from tools.planning import register_planning_tools


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class SequenceBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def chat(self, messages, tools=None):
        self.requests.append(messages)
        response = self.responses.pop(0)
        if callable(response):
            response = response(messages)
        return {
            "role": "assistant",
            "content": response.get("content", ""),
            "tool_calls": response.get("tool_calls", []),
            "usage": response.get("usage", {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}),
        }


def tool_call(name, **arguments):
    return {"content": "", "tool_calls": [{"id": f"call-{name}", "name": name, "arguments": arguments}]}


class MemoryTests(unittest.TestCase):
    def test_markdown_memory_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MEMORY.md"
            Memory(path).write("时间戳使用 UTC")
            self.assertIn("UTC", Memory(path).recall())
            self.assertFalse(path.with_suffix(".md.tmp").exists())

    def test_kv_memory_overwrites_recalls_and_forgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = KVMemory(path)
            memory.remember("package_manager", "npm")
            memory.remember("package_manager", "pnpm")
            memory.remember("video_style", "教程视频保留操作步骤")
            reloaded = KVMemory(path)
            self.assertIn("pnpm", reloaded.recall("package_manager"))
            self.assertEqual(reloaded.data["package_manager"]["value"], "pnpm")
            self.assertTrue(reloaded.forget("package_manager"))
            self.assertNotIn("package_manager", KVMemory(path).data)

    def test_memory_rejects_secrets_and_bulk_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = KVMemory(Path(tmp) / "memory.json")
            with self.assertRaises(ValueError):
                memory.remember("api", "api_key=sk-abcdefghijklmnopqrstuvwxyz")
            with self.assertRaises(ValueError):
                memory.remember("video", "# transcript_source: ASR full transcript")
            self.assertFalse(memory.path.exists())

    def test_runtime_memory_directory_is_gitignored(self):
        ignore = Path(__file__).parents[1] / ".gitignore"
        self.assertIn(".mini-openclaw/", ignore.read_text(encoding="utf-8"))


class PlanningTests(unittest.TestCase):
    def test_todo_state_machine(self):
        todo = TodoList()
        todo.write(["分析", "验证"])
        todo.update(1, "in_progress")
        todo.update(1, "completed")
        inserted = todo.insert("记录结果")
        todo.update(2, "completed")
        todo.update(inserted, "completed")
        self.assertTrue(todo.all_done())
        self.assertIn("[x] 3 记录结果", todo.render())

    def test_force_plan_injects_todo_and_blocks_business_tool_before_plan(self):
        with tempfile.TemporaryDirectory() as tmp, working_directory(Path(tmp)):
            calls = {"read": 0}
            registry = ToolRegistry()
            registry.register(Tool("read", "read", {"type": "object"}, lambda **_: calls.__setitem__("read", calls["read"] + 1) or "data"))
            todo = TodoList()
            register_planning_tools(registry, todo)
            backend = SequenceBackend([
                tool_call("read", path="README.md"),
                tool_call("todo_write", items=["读取文件"]),
                tool_call("update_todo", id=1, status="in_progress"),
                tool_call("update_todo", id=1, status="completed"),
                {"content": "done", "tool_calls": []},
            ])
            agent = AgentLoop(
                backend,
                registry,
                "system",
                todo=todo,
                planning_mode="force",
                tool_policy=ToolPolicy(),
                max_turns=8,
            )
            self.assertEqual(agent.run("task"), "done")
            self.assertEqual(calls["read"], 0)
            self.assertTrue(any("# 当前任务清单" in str(message.get("content")) for message in backend.requests[2]))
            self.assertTrue(todo.all_done())

    def test_transient_read_retries_three_times(self):
        attempts = {"count": 0}

        def flaky_read(**_kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise TimeoutError("temporary")
            return "ok"

        registry = ToolRegistry()
        registry.register(Tool("read", "read", {"type": "object"}, flaky_read))
        backend = SequenceBackend([tool_call("read", path="a.txt"), {"content": "done", "tool_calls": []}])
        result = AgentLoop(backend, registry, "system", tool_policy=ToolPolicy()).run("task")
        self.assertEqual(result, "done")
        self.assertEqual(attempts["count"], 3)

    def test_repeat_guard_blocks_third_identical_call(self):
        calls = {"count": 0}
        registry = ToolRegistry()
        registry.register(Tool("read", "read", {"type": "object"}, lambda **_: calls.__setitem__("count", calls["count"] + 1) or "ok"))
        backend = SequenceBackend([
            tool_call("read", path="a.txt"),
            tool_call("read", path="a.txt"),
            tool_call("read", path="a.txt"),
            {"content": "stopped", "tool_calls": []},
        ])
        result = AgentLoop(backend, registry, "system", tool_policy=ToolPolicy()).run("task")
        self.assertEqual(result, "stopped")
        self.assertEqual(calls["count"], 2)

    def test_max_turns_reports_bounded_stop(self):
        registry = ToolRegistry()
        registry.register(Tool("read", "read", {"type": "object"}, lambda **_: "ok"))
        backend = SequenceBackend([tool_call("read", path=f"{index}.txt") for index in range(3)])
        result = AgentLoop(backend, registry, "system", tool_policy=ToolPolicy(), max_turns=3).run("task")
        self.assertIn("3/3", result)

    def test_successful_terminal_artifact_blocks_duplicate_write(self):
        calls = {"count": 0}
        registry = ToolRegistry()
        registry.register(Tool(
            "kb_write", "write kb", {"type": "object"},
            lambda **_: calls.__setitem__("count", calls["count"] + 1) or '{"ok": true}',
        ))
        todo = TodoList()
        todo.write(["生成知识库"])
        todo.update(1, "completed")
        args = {"source_url": "https://www.bilibili.com/video/BV1CURRENT1/", "transcript": "text"}
        backend = SequenceBackend([
            tool_call("kb_write", **args),
            tool_call("kb_write", **args),
            {"content": "done", "tool_calls": []},
        ])
        agent = AgentLoop(
            backend,
            registry,
            "system",
            todo=todo,
            planning_mode="force",
            tool_policy=ToolPolicy(video_mode=True, task="BV1CURRENT1"),
        )
        self.assertEqual(agent.run("task"), "done")
        self.assertEqual(calls["count"], 1)


class TracerTests(unittest.TestCase):
    def test_trace_records_usage_redacts_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            tracer = Tracer(path)
            first = tracer.observe_request([{"role": "system", "content": "stable"}], [])
            second = tracer.observe_request([
                {"role": "system", "content": "stable"},
                {"role": "user", "content": "next"},
            ], [])
            self.assertEqual(first, 0)
            self.assertGreater(second, 0)
            tracer.call(
                "llm",
                "decide",
                lambda: {
                    "content": "api_key=sk-abcdefghijklmnopqrstuvwxyz",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                },
                input_data={"token": "sk-abcdefghijklmnopqrstuvwxyz"},
                meta={"prefix_chars": second},
            )
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", text)
            self.assertIn("[REDACTED]", text)
            self.assertIn("decide", replay(path))
            with patch.dict(os.environ, {
                "MODEL_INPUT_USD_PER_1M": "1.0",
                "MODEL_OUTPUT_USD_PER_1M": "2.0",
            }):
                report = cost_report(path)
            self.assertEqual(report["total_tokens"], 120)
            self.assertEqual(report["estimated_cost_usd"], 0.00014)

    def test_backend_preserves_api_usage(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "model": "test-model",
                    "choices": [{"message": {"content": "ok", "tool_calls": []}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
                }

        class Client:
            def post(self, *_args, **_kwargs):
                return Response()

        backend = object.__new__(DeepSeekBackend)
        backend.api_key = "test"
        backend.base_url = "https://example.com"
        backend.model = "test-model"
        backend._client = Client()
        result = backend.chat([{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(result["usage"]["total_tokens"], 14)
        self.assertEqual(result["model"], "test-model")


class AblationTests(unittest.TestCase):
    def test_complete_cache_reuse_counts_as_success_without_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            kb = Path(tmp)
            for name in ("index.md", "metadata.json", "transcript.txt", "chunks.jsonl"):
                (kb / name).write_text("ok\n", encoding="utf-8")
            spans = [
                {"kind": "tool", "name": "video_probe", "ok": True},
                {"kind": "tool", "name": "read", "ok": True},
                {"kind": "run", "name": "completed", "ok": True},
            ]
            self.assertTrue(successful_cached_run(0, "done", spans, kb))
            self.assertFalse(successful_cached_run(0, "达到最大轮数", spans, kb))

    def test_external_api_errors_are_not_agent_failures(self):
        self.assertEqual(classify_outcome(1, "402 Payment Required", False), "external_error")
        self.assertEqual(classify_outcome(124, "timed out", False), "external_error")
        self.assertEqual(classify_outcome(1, "tool failed", False), "agent_failure")
        self.assertEqual(classify_outcome(0, "done", True), "success")

    def test_report_excludes_external_errors_from_valid_sample_average(self):
        base = {
            "returncode": 0,
            "llm_steps": 2,
            "tool_steps": 1,
            "todo_calls": 0,
            "repeat_guards": 0,
            "estimated_cost_usd": 0.0,
        }
        records = [
            {**base, "mode": "plan", "run": 1, "success": True, "outcome": "success", "elapsed_seconds": 10, "total_tokens": 100},
            {**base, "mode": "plan", "run": 2, "success": False, "outcome": "external_error", "elapsed_seconds": 1, "total_tokens": 0},
            {**base, "mode": "no-plan", "run": 1, "success": True, "outcome": "success", "elapsed_seconds": 5, "total_tokens": 50},
        ]
        report = render_report(records, "BV1TEST")
        self.assertIn("有效样本 1/2，外部错误 1", report)
        self.assertIn("平均 Token 100.0", report)


if __name__ == "__main__":
    unittest.main()
