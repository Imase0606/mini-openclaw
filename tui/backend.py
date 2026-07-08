"""流式后端：为 DeepSeek API 添加 SSE 流式支持。

不修改 backend/client.py，作为独立适配层。
"""

from __future__ import annotations
import json
import os
from typing import Any, Generator

import httpx


class StreamingBackend:
    """DeepSeek API 的流式包装器。

    使用 SSE (Server-Sent Events) 实现 token 级流式输出。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (
            base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        ).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.timeout = timeout

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
    ) -> Generator[dict[str, Any], None, None]:
        """SSE 流式聊天补全。yield 事件字典：

        - {"type": "content", "delta": "..."}            — 文本 token
        - {"type": "tool_call", "id": "...",
           "name": "...", "arguments": {...}}            — 完整工具调用
        - {"type": "usage", "prompt_tokens": N,
           "completion_tokens": M}                        — token 用量
        - {"type": "done"}                                 — 流结束
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            ) as resp:
                resp.raise_for_status()

                # tool call 分块累积
                acc_tool_calls: dict[int, dict[str, Any]] = {}

                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        # 输出累积的 tool_call
                        for entry in acc_tool_calls.values():
                            try:
                                args = json.loads(entry.get("arguments", "{}"))
                            except json.JSONDecodeError:
                                args = {}
                            yield {
                                "type": "tool_call",
                                "id": entry.get("id", ""),
                                "name": entry.get("name", ""),
                                "arguments": args,
                            }
                        yield {"type": "done"}
                        return

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta", {})
                    finish_reason = choice.get("finish_reason")

                    # 文本 token
                    if delta.get("content"):
                        yield {"type": "content", "delta": delta["content"]}

                    # tool call 分块累积
                    if delta.get("tool_calls"):
                        for tc_delta in delta["tool_calls"]:
                            idx = tc_delta.get("index", 0)
                            if idx not in acc_tool_calls:
                                acc_tool_calls[idx] = {
                                    "id": tc_delta.get("id", f"call_{idx}"),
                                    "name": "",
                                    "arguments": "",
                                }
                            entry = acc_tool_calls[idx]
                            if tc_delta.get("id"):
                                entry["id"] = tc_delta["id"]
                            fn = tc_delta.get("function", {})
                            if fn.get("name"):
                                entry["name"] = fn["name"]
                            if fn.get("arguments"):
                                entry["arguments"] += fn["arguments"]

                    # token 用量（最后一块）
                    if data.get("usage"):
                        yield {"type": "usage", **data["usage"]}

                    # 结束原因
                    if finish_reason == "tool_calls":
                        for entry in acc_tool_calls.values():
                            try:
                                args = json.loads(entry.get("arguments", "{}"))
                            except json.JSONDecodeError:
                                args = {}
                            yield {
                                "type": "tool_call",
                                "id": entry.get("id", ""),
                                "name": entry.get("name", ""),
                                "arguments": args,
                            }
                        yield {"type": "done"}
                        return
                    elif finish_reason == "stop":
                        yield {"type": "done"}
                        return

    def _to_openai_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """将内部消息格式转为 OpenAI API 格式。"""
        out = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                out.append({
                    "role": "tool",
                    "content": str(m.get("content", "")),
                    "tool_call_id": m.get("tool_call_id", m.get("name", "tool")),
                })
            elif role == "assistant" and m.get("tool_calls"):
                tcs = []
                for i, c in enumerate(m["tool_calls"]):
                    tcs.append({
                        "id": c.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("arguments", {}), ensure_ascii=False),
                        },
                    })
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": tcs,
                })
            else:
                out.append({"role": role, "content": m.get("content", "")})
        return out


class FakeBackendStreamAdapter:
    """为 FakeBackend 添加流式适配，使 TUI 在无 API key 时也能运行。"""

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,  # noqa: ARG002
    ) -> Generator[dict[str, Any], None, None]:
        """模拟流式输出，一次性 yield 全部内容和工具调用。"""
        from backend.fake_backend import FakeBackend

        fb = FakeBackend()
        result = fb.chat(messages, tools)

        content = result.get("content", "")
        tool_calls = result.get("tool_calls", [])

        # 逐步 yield 内容（模拟流式）
        if content:
            words = content.split(" ")
            for i, word in enumerate(words):
                yield {"type": "content", "delta": word + (" " if i < len(words) - 1 else "")}

        # yield 工具调用
        for tc in tool_calls:
            yield {
                "type": "tool_call",
                "id": tc.get("id", "call_0"),
                "name": tc["name"],
                "arguments": tc.get("arguments", {}),
            }

        yield {"type": "done"}


def get_streaming_backend() -> StreamingBackend | FakeBackendStreamAdapter:
    """获取最佳可用后端。

    优先使用 DeepSeek API（流式）；API key 不可用时回退 FakeBackend。
    """
    try:
        backend = StreamingBackend()
        if backend.api_key and backend.api_key != "sk-":
            return backend
    except Exception:
        pass
    print("[提示] 未检测到 DEEPSEEK_API_KEY，使用 FakeBackend（模拟模式）")
    return FakeBackendStreamAdapter()
