"""UI-agnostic events emitted by the agent runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    content: str
    messages: list[dict[str, Any]]
    status: str
    turns: int
