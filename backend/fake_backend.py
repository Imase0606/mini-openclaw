"""一个"假后端"，用于未配 DeepSeek key 时离线跑通骨架。

它实现和真后端 backend/client.py（DeepSeekBackend）一样的最小接口：
  chat(messages, tools) -> {"role": "assistant", "content": ..., "tool_calls": [...] }

行为：用极简规则模拟一个会调用工具的模型，让 selfcheck / 主循环骨架能跑。
配好 DEEPSEEK_API_KEY 后，agent/cli.py 会自动改用真模型（DeepSeekBackend）。
"""
from __future__ import annotations
from typing import Any


class FakeBackend:
    """规则驱动的假模型：只为打通管道，不要当真。"""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None) -> dict[str, Any]:
        last = messages[-1]["content"] if messages else ""
        if isinstance(last, list) and any(
            isinstance(block, dict) and block.get("type") == "image"
            for block in last
        ):
            return {
                "role": "assistant",
                "content": "[FakeBackend] 已收到图像内容块；真实识图需要配置支持视觉的模型。",
                "tool_calls": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        # 如果上一条是工具结果（observation），就给最终答复
        if messages and messages[-1].get("role") == "tool":
            return {"role": "assistant", "content": f"[FakeBackend] 已根据工具结果完成：{last[:60]}", "tool_calls": [],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

        # 否则，如果有可用工具且用户像是要做事，假装调一个工具
        if tools and any(k in str(last) for k in ("文件", "运行", "file", "run", "hello")):
            name = tools[0]["function"]["name"]
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": name, "arguments": {}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        return {"role": "assistant", "content": "[FakeBackend] 你好，我是离线占位后端。配好 DEEPSEEK_API_KEY 即用真模型。", "tool_calls": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    def chat_stream(self, messages, tools=None, temperature: float = 0.0):  # noqa: ARG002
        result = self.chat(messages, tools)
        content = result.get("content", "")
        if content:
            yield {"type": "content", "delta": content}
        for call in result.get("tool_calls") or []:
            yield {"type": "tool_call", **call}
        yield {"type": "usage", **result.get("usage", {})}
        yield {"type": "done"}
