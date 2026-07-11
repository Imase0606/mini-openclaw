"""Todo tools bound to one AgentLoop planning state."""
from __future__ import annotations

from agent.planning import TodoList
from .base import Tool, ToolRegistry


def register_planning_tools(registry: ToolRegistry, todo: TodoList) -> None:
    def todo_write(items: list[str] | None = None) -> str:
        try:
            todo.write(items or [])
        except ValueError as exc:
            return f"[规划错误] {exc}"
        return todo.render()

    def update_todo(id: int = 0, status: str = "") -> str:
        try:
            todo.update(int(id), status)
        except (TypeError, ValueError) as exc:
            return f"[规划错误] {exc}"
        return todo.render()

    def insert_todo(text: str = "") -> str:
        try:
            todo.insert(text)
        except ValueError as exc:
            return f"[规划错误] {exc}"
        return todo.render()

    registry.register(Tool(
        "todo_write",
        "面对复杂多步任务时，先用它写下有序子任务；简单问答和短任务不需要。",
        {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            "required": ["items"],
        },
        todo_write,
    ))
    registry.register(Tool(
        "update_todo",
        "开始、完成或阻塞某条子任务时更新状态，确保清单反映真实进度。",
        {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                },
            },
            "required": ["id", "status"],
        },
        update_todo,
    ))
    registry.register(Tool(
        "insert_todo",
        "错误恢复或重规划时插入新的子任务。",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        insert_todo,
    ))
