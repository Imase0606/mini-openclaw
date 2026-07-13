from __future__ import annotations

import json
import unittest

from agent.events import AgentEvent
from agent.loop import AgentLoop
from agent.policy import ToolPolicy
from backend.client import DeepSeekBackend
from tools.base import Tool, ToolRegistry
from tools.external import wrap_external
from tui.widgets import ToolCallCard


class StreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        return iter(self.lines)


class StreamClient:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.request_json = None

    def stream(self, *_args, **_kwargs):
        self.request_json = _kwargs.get("json")
        return StreamResponse(self.lines)


def stream_backend(lines: list[str]) -> DeepSeekBackend:
    backend = object.__new__(DeepSeekBackend)
    backend.api_key = "test"
    backend.base_url = "https://example.com"
    backend.model = "stream-test"
    backend._client = StreamClient(lines)
    return backend


class RecoveringBackend:
    def __init__(self, first_call: dict) -> None:
        self.first_call = first_call
        self.requests: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.requests.append(messages)
        if len(self.requests) == 1:
            return {"content": "", "tool_calls": [self.first_call]}
        if len(self.requests) == 2:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "kb-ok",
                    "name": "kb_write",
                    "arguments": {
                        "source_url": "https://www.bilibili.com/video/BV1TEST123/",
                    },
                }],
            }
        return {"content": "done", "tool_calls": []}


def kb_registry(calls: list[dict]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool(
        "kb_write",
        "test knowledge writer",
        {
            "type": "object",
            "properties": {"source_url": {"type": "string"}},
            "required": ["source_url"],
        },
        lambda **arguments: calls.append(arguments) or '{"markdown_path":"knowledge_base/BV1TEST123/index.md"}',
    ))
    return registry


def ready_video_policy() -> ToolPolicy:
    policy = ToolPolicy(video_mode=True, task="BV1TEST123")
    policy.observe("video_frame_ocr", json.dumps({
        "bvid": "BV1TEST123", "visual_status": "completed",
    }))
    return policy


class ToolArgumentParsingTests(unittest.TestCase):
    def test_visual_tool_emits_artifacts_and_card_summary(self):
        events: list[AgentEvent] = []
        loop = AgentLoop(
            RecoveringBackend({}), ToolRegistry(), "system", event_sink=events.append,
        )
        payload = {
            "visual_status": "completed",
            "visual_backend": "mimo",
            "frames_sampled": 12,
            "records": 5,
            "visual_notes_path": "knowledge_base/BV1TEST123/visual_notes.jsonl",
            "contact_sheet_path": "knowledge_base/BV1TEST123/visual_contact_sheet.jpg",
        }
        loop._emit_artifacts(
            "video_frame_ocr",
            wrap_external(json.dumps(payload), "https://www.bilibili.com/video/BV1TEST123/"),
        )

        paths = [event.data["path"] for event in events if event.kind == "artifact"]
        self.assertEqual(paths, [payload["visual_notes_path"], payload["contact_sheet_path"]])
        self.assertEqual(ToolCallCard._visual_summary(json.dumps(payload)), "  completed/mimo 12f 5r")

    def test_stream_accumulates_fragmented_kb_write_arguments(self):
        backend = stream_backend([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"kb-1",'
            '"function":{"name":"kb_write","arguments":"{\\"source_url\\":\\"https://www.bili"}}]},'
            '"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":'
            '{"arguments":"bili.com/video/BV1TEST123/\\"}"}}]},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ])

        tool = next(
            event for event in backend.chat_stream([{"role": "user", "content": "hi"}], [])
            if event["type"] == "tool_call"
        )

        self.assertEqual(tool["arguments"], {
            "source_url": "https://www.bilibili.com/video/BV1TEST123/",
        })
        self.assertNotIn("arguments_error", tool)
        self.assertEqual(backend._client.request_json["max_tokens"], 4096)

    def test_length_finish_reason_reports_truncated_arguments(self):
        backend = stream_backend([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"kb-1",'
            '"function":{"name":"kb_write","arguments":"{\\"source_url\\":\\"https://example.com"}}]},'
            '"finish_reason":"length"}]}',
            "data: [DONE]",
        ])

        tool = next(
            event for event in backend.chat_stream([{"role": "user", "content": "hi"}], [])
            if event["type"] == "tool_call"
        )

        self.assertEqual(tool["arguments"], {})
        self.assertEqual(tool["arguments_error"]["type"], "OutputTruncated")
        self.assertEqual(tool["arguments_error"]["finish_reason"], "length")

    def test_stream_preserves_truncated_tool_argument_error(self):
        backend = stream_backend([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"kb-1",'
            '"function":{"name":"kb_write","arguments":"{\\"source_url\\":\\"https://example.com"}}]},'
            '"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ])

        tool = next(
            event for event in backend.chat_stream([{"role": "user", "content": "hi"}], [])
            if event["type"] == "tool_call"
        )

        self.assertEqual(tool["arguments"], {})
        self.assertEqual(tool["arguments_error"]["type"], "JSONDecodeError")
        self.assertGreater(tool["arguments_error"]["length"], 0)
        self.assertIn("position", tool["arguments_error"])

    def test_non_object_tool_arguments_are_rejected(self):
        message = {
            "content": "",
            "tool_calls": [{
                "id": "kb-list",
                "function": {"name": "kb_write", "arguments": "[]"},
            }],
        }

        tool = DeepSeekBackend._normalize(message)["tool_calls"][0]

        self.assertEqual(tool["arguments"], {})
        self.assertEqual(tool["arguments_error"]["type"], "TypeError")
        self.assertIn("顶层必须是对象", tool["arguments_error"]["message"])

    def test_malformed_sse_packet_is_reported(self):
        backend = stream_backend(['data: {"choices":['])
        with self.assertRaisesRegex(RuntimeError, "SSE 数据包 JSON 解析失败"):
            list(backend.chat_stream([{"role": "user", "content": "hi"}], []))

    def test_empty_object_is_parameter_error_not_permission_denial(self):
        calls: list[dict] = []
        events: list[AgentEvent] = []
        backend = RecoveringBackend({
            "id": "kb-empty",
            "name": "kb_write",
            "arguments": {},
        })
        loop = AgentLoop(
            backend,
            kb_registry(calls),
            "system",
            tool_policy=ready_video_policy(),
            event_sink=events.append,
        )

        result = loop.run_turn("https://www.bilibili.com/video/BV1TEST123/")

        self.assertEqual(result.content, "done")
        self.assertEqual(len(calls), 1)
        failures = [event for event in events if event.kind == "tool_finished"]
        self.assertEqual(failures[0].data["status"], "error")
        self.assertIn("[参数层]", failures[0].data["result"])
        self.assertNotIn("[权限层]", failures[0].data["result"])
        self.assertTrue(any(
            "缺少必需参数" in str(message.get("content"))
            for message in backend.requests[1]
        ))

    def test_parse_error_reaches_agent_and_recovers_once(self):
        calls: list[dict] = []
        backend = RecoveringBackend({
            "id": "kb-bad-json",
            "name": "kb_write",
            "arguments": {},
            "arguments_error": {
                "type": "JSONDecodeError",
                "message": "Unterminated string",
                "position": 42,
                "length": 57,
            },
        })
        loop = AgentLoop(
            backend,
            kb_registry(calls),
            "system",
            tool_policy=ready_video_policy(),
        )

        result = loop.run_turn("https://www.bilibili.com/video/BV1TEST123/")

        self.assertEqual(result.content, "done")
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(
            "JSON 解析失败" in str(message.get("content"))
            and "错误位置 42" in str(message.get("content"))
            for message in backend.requests[1]
        ))

    def test_output_truncation_reaches_agent_and_recovers_once(self):
        calls: list[dict] = []
        backend = RecoveringBackend({
            "id": "kb-truncated",
            "name": "kb_write",
            "arguments": {},
            "arguments_error": {
                "type": "OutputTruncated",
                "message": "模型因输出长度上限停止",
                "length": 4096,
                "finish_reason": "length",
            },
        })
        loop = AgentLoop(
            backend,
            kb_registry(calls),
            "system",
            tool_policy=ready_video_policy(),
        )

        result = loop.run_turn("https://www.bilibili.com/video/BV1TEST123/")

        self.assertEqual(result.content, "done")
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(
            "输出长度上限被截断" in str(message.get("content"))
            and "已接收参数长度 4096" in str(message.get("content"))
            for message in backend.requests[1]
        ))


if __name__ == "__main__":
    unittest.main()
