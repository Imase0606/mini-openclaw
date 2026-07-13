"""大模型后端：DeepSeek API 客户端（OpenAI 兼容）。

本课程的 mini-OpenClaw 不本地部署模型，而是调用 DeepSeek API 作为"大脑"。
DeepSeek 的接口与 OpenAI 完全兼容，所以下面用通用的 OpenAI 协议写法，
只要改 base_url / api_key / model 就能换任意 OpenAI 兼容厂商。

接口约定（和 FakeBackend 一致，主循环 agent/loop.py 只认这个）：
    chat(messages, tools) -> {"role": "assistant", "content": str, "tool_calls": [ {name, arguments}, ... ]}

环境变量：
    DEEPSEEK_API_KEY   你的 key（千万别提交进 git！）
    DEEPSEEK_BASE_URL  默认 https://api.deepseek.com
    DEEPSEEK_MODEL     默认 deepseek-chat
    VISION_API_KEY     CLI --image 使用的 OpenAI-compatible 视觉模型密钥
    VISION_BASE_URL    视觉模型 API 根地址
    VISION_MODEL       视觉模型名称
"""
from __future__ import annotations
import os
import json
from typing import Any, Iterator

import httpx


def _decode_tool_arguments(raw: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Decode tool arguments without hiding malformed provider output."""
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str):
        return {}, {
            "type": "TypeError",
            "message": "工具参数必须是 JSON 对象",
            "length": 0,
        }
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, {
            "type": type(exc).__name__,
            "message": exc.msg,
            "position": exc.pos,
            "line": exc.lineno,
            "column": exc.colno,
            "length": len(raw),
        }
    if not isinstance(parsed, dict):
        return {}, {
            "type": "TypeError",
            "message": "工具参数 JSON 顶层必须是对象",
            "length": len(raw),
        }
    return parsed, None


class DeepSeekBackend:
    def __init__(self,
                 api_key: str | None = None,
                 base_url: str | None = None,
                 model: str | None = None,
                 timeout: float | None = None,
                 max_output_tokens: int | None = None):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        if not self.api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")
        request_timeout = timeout or float(os.environ.get("DEEPSEEK_TIMEOUT", "180"))
        configured_max = max_output_tokens or int(os.environ.get("MODEL_MAX_OUTPUT_TOKENS", "4096"))
        self.max_output_tokens = max(256, configured_max)
        self._client = httpx.Client(
            timeout=httpx.Timeout(request_timeout, connect=min(20.0, request_timeout)),
        )

    def close(self) -> None:
        """Release the shared HTTP connection pool."""
        self._client.close()

    @property
    def chat_completions_url(self) -> str:
        suffix = "/chat/completions" if self.base_url.endswith("/v1") else "/v1/chat/completions"
        return self.base_url + suffix

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None,
             temperature: float = 0.0) -> dict[str, Any]:
        """一次（非流式）对话补全，返回归一化的 assistant 消息。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "max_tokens": getattr(self, "max_output_tokens", 4096),
        }
        if tools:
            payload["tools"] = tools           # OpenAI tools 格式，base.Tool.schema() 已生成
            payload["tool_choice"] = "auto"

        has_images = any(self._content_has_image(message.get("content")) for message in messages)
        resp = self._client.post(
            self.chat_completions_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if has_images:
                detail = resp.text[:1000]
                raise RuntimeError(
                    "视觉模型请求失败。请确认当前模型支持图像输入，或配置 "
                    "VISION_API_KEY / VISION_BASE_URL / VISION_MODEL。"
                    f" API 返回：{detail}"
                ) from exc
            raise
        payload_out = resp.json()
        choice = payload_out["choices"][0]
        msg = choice["message"]
        normalized = self._normalize(msg, finish_reason=choice.get("finish_reason"))
        usage = payload_out.get("usage") or {}
        normalized["usage"] = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
        normalized["model"] = payload_out.get("model") or self.model
        return normalized

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
    ) -> Iterator[dict[str, Any]]:
        """Yield normalized text, tool-call and usage events from OpenAI SSE."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": getattr(self, "max_output_tokens", 4096),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        accumulated: dict[int, dict[str, Any]] = {}
        emitted = False
        final_finish_reason = ""
        with self._client.stream(
            "POST",
            self.chat_completions_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    packet = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "SSE 数据包 JSON 解析失败："
                        f"{exc.msg}（位置 {exc.pos}，长度 {len(raw)}）"
                    ) from exc
                usage = packet.get("usage") or {}
                if usage:
                    yield {
                        "type": "usage",
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    }
                for choice in packet.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield {"type": "content", "delta": delta["content"]}
                    for fragment in delta.get("tool_calls") or []:
                        index = int(fragment.get("index") or 0)
                        entry = accumulated.setdefault(index, {
                            "id": fragment.get("id") or f"call_{index}",
                            "name": "",
                            "arguments": "",
                        })
                        if fragment.get("id"):
                            entry["id"] = fragment["id"]
                        function = fragment.get("function") or {}
                        entry["name"] += function.get("name") or ""
                        entry["arguments"] += function.get("arguments") or ""
                    finish_reason = str(choice.get("finish_reason") or "")
                    if finish_reason:
                        final_finish_reason = finish_reason
                    if finish_reason == "tool_calls":
                        yield from self._stream_tool_calls(accumulated, finish_reason=finish_reason)
                        emitted = True
        if accumulated and not emitted:
            yield from self._stream_tool_calls(accumulated, finish_reason=final_finish_reason)
        yield {"type": "done"}

    @staticmethod
    def _stream_tool_calls(
        accumulated: dict[int, dict[str, Any]],
        *,
        finish_reason: str = "",
    ) -> Iterator[dict[str, Any]]:
        for index in sorted(accumulated):
            entry = accumulated[index]
            arguments, arguments_error = _decode_tool_arguments(entry["arguments"])
            if finish_reason == "length":
                arguments = {}
                arguments_error = {
                    "type": "OutputTruncated",
                    "message": "模型因输出长度上限停止，工具参数可能不完整",
                    "length": len(entry["arguments"]),
                    "finish_reason": finish_reason,
                }
            event = {
                "type": "tool_call",
                "id": entry["id"],
                "name": entry["name"],
                "arguments": arguments,
            }
            if arguments_error:
                event["arguments_error"] = arguments_error
            yield event

    # --- 把内部 messages（含 role=tool）转成 OpenAI 标准格式 ---
    def _to_openai_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                # OpenAI 要求 tool 消息带 tool_call_id；最小实现可用 name 兜底
                out.append({"role": "tool", "content": str(m.get("content", "")),
                            "tool_call_id": m.get("tool_call_id", m.get("name", "tool"))})
            elif role == "assistant" and m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("content") or None,
                            "tool_calls": self._to_openai_tool_calls(m["tool_calls"])})
            else:
                out.append({"role": role, "content": self._to_openai_content(m.get("content", ""))})
        return out

    @staticmethod
    def _content_has_image(content: Any) -> bool:
        return isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") in {"image", "image_url"}
            for block in content
        )

    @staticmethod
    def _to_openai_content(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        converted: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "image":
                converted.append(block)
                continue
            source = block.get("source") or {}
            if source.get("type") != "base64":
                raise ValueError("仅支持 base64 图片内容块")
            media_type = source.get("media_type") or "image/png"
            data = source.get("data") or ""
            converted.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            })
        return converted

    @staticmethod
    def _to_openai_tool_calls(calls: list[dict]) -> list[dict]:
        out = []
        for i, c in enumerate(calls):
            out.append({"id": c.get("id", f"call_{i}"), "type": "function",
                        "function": {"name": c["name"],
                                     "arguments": json.dumps(c.get("arguments", {}), ensure_ascii=False)}})
        return out

    # --- 把 OpenAI 返回归一化成内部格式 ---
    @staticmethod
    def _normalize(
        msg: dict[str, Any],
        *,
        finish_reason: str = "",
    ) -> dict[str, Any]:
        tool_calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args, arguments_error = _decode_tool_arguments(fn.get("arguments"))
            if finish_reason == "length":
                raw = fn.get("arguments")
                args = {}
                arguments_error = {
                    "type": "OutputTruncated",
                    "message": "模型因输出长度上限停止，工具参数可能不完整",
                    "length": len(raw) if isinstance(raw, str) else 0,
                    "finish_reason": finish_reason,
                }
            call = {"id": tc.get("id"), "name": fn.get("name"), "arguments": args}
            if arguments_error:
                call["arguments_error"] = arguments_error
            tool_calls.append(call)
        return {"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls}
