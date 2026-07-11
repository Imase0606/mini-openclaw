"""Memory tools bound to one runtime KVMemory instance."""
from __future__ import annotations

from agent.memory import KVMemory
from .base import Tool, ToolRegistry


def register_memory_tools(registry: ToolRegistry, memory: KVMemory) -> None:
    def remember(key: str = "", value: str = "") -> str:
        try:
            memory.remember(key, value)
        except ValueError as exc:
            return f"[记忆拒绝] {exc}"
        return f"已记住：{key}"

    def forget_memory(key: str = "") -> str:
        return f"已遗忘：{key}" if memory.forget(key) else f"未找到记忆：{key}"

    def recall_memory(query: str = "") -> str:
        return memory.recall(query) or "[无相关长期记忆]"

    registry.register(Tool(
        "remember",
        "仅当用户明确要求长期记住稳定偏好、项目约定或关键决策时调用；禁止保存密钥、转写和外部大段内容。",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "稳定、可覆盖的记忆键"},
                "value": {"type": "string", "description": "需要跨会话保留的简短内容"},
            },
            "required": ["key", "value"],
        },
        remember,
    ))
    registry.register(Tool(
        "forget_memory",
        "当用户明确要求遗忘某条长期记忆时调用。",
        {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
        forget_memory,
    ))
    registry.register(Tool(
        "recall_memory",
        "查询运行时长期记忆；只读，不要用它读取 transcript 或知识库。",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        recall_memory,
    ))
