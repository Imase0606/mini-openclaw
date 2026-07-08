"""对话状态与事件类型。"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from enum import Enum, auto


class MessageRole(Enum):
    USER = auto()
    ASSISTANT = auto()


class ToolStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    DENIED = "denied"


@dataclass
class ToolCallRecord:
    """一次工具调用的记录。"""
    call_id: str
    name: str
    arguments: dict
    result: str | None = None
    status: ToolStatus = ToolStatus.PENDING
    duration_ms: float = 0.0


@dataclass
class MessageRecord:
    """对话中的一条消息。"""
    role: MessageRole
    content: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    token_usage: dict | None = None


@dataclass
class ConversationState:
    """完整对话状态（独立于 UI 的数据层）。"""
    messages: list[MessageRecord] = field(default_factory=list)
    current_turn: int = 0

    def add_user_message(self, content: str) -> MessageRecord:
        msg = MessageRecord(role=MessageRole.USER, content=content)
        self.messages.append(msg)
        return msg

    def add_assistant_message(self) -> MessageRecord:
        msg = MessageRecord(role=MessageRole.ASSISTANT)
        self.messages.append(msg)
        return msg

    def add_tool_call(self, msg: MessageRecord, name: str, args: dict) -> ToolCallRecord:
        tc = ToolCallRecord(call_id=f"call_{len(msg.tool_calls)}", name=name, arguments=args)
        msg.tool_calls.append(tc)
        return tc


# ---- Worker → UI 事件类型 ----

@dataclass
class TokenEvent:
    """流式文本块。"""
    text: str


@dataclass
class ToolCallStartEvent:
    """模型请求调用工具。"""
    call_id: str
    name: str
    arguments: dict


@dataclass
class ToolCallEndEvent:
    """工具执行完毕。"""
    call_id: str
    result: str
    duration_ms: float


@dataclass
class StatusEvent:
    """Agent 状态变化。"""
    status: str  # thinking, executing, done, error, cancelled


@dataclass
class ErrorEvent:
    """发生错误。"""
    message: str


@dataclass
class DoneEvent:
    """任务完成。"""
    final_content: str


@dataclass
class TurnEvent:
    """一轮结束（工具调用完成后、下一轮开始前）。"""
    turn: int


@dataclass
class TokenUsageEvent:
    """Token 用量。"""
    prompt_tokens: int
    completion_tokens: int
