"""Single-run todo state machine for long-running agent tasks."""
from __future__ import annotations

from typing import Any


STATUSES = {"pending", "in_progress", "completed", "blocked"}
MARKS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "blocked": "[!]"}


class TodoList:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.version = 0

    def write(self, texts: list[str]) -> None:
        cleaned = [" ".join(str(text).strip().split()) for text in texts]
        cleaned = [text for text in cleaned if text]
        if not cleaned:
            raise ValueError("Todo 清单不能为空")
        if len(cleaned) > 20:
            raise ValueError("Todo 清单最多 20 项")
        self.items = [
            {"id": index, "text": text[:300], "status": "pending", "attempts": 0}
            for index, text in enumerate(cleaned, 1)
        ]
        self.version += 1

    def update(self, item_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"无效 Todo 状态：{status}")
        target = self.get(item_id)
        if target is None:
            raise ValueError(f"未找到 Todo：{item_id}")
        if status == "in_progress":
            for item in self.items:
                if item["status"] == "in_progress" and item["id"] != item_id:
                    item["status"] = "pending"
        if target["status"] != status:
            target["status"] = status
            self.version += 1

    def insert(self, text: str) -> int:
        clean = " ".join(str(text or "").strip().split())
        if not clean:
            raise ValueError("Todo 文本不能为空")
        item_id = max((item["id"] for item in self.items), default=0) + 1
        self.items.append({"id": item_id, "text": clean[:300], "status": "pending", "attempts": 0})
        self.version += 1
        return item_id

    def get(self, item_id: int) -> dict[str, Any] | None:
        return next((item for item in self.items if item["id"] == item_id), None)

    def current(self) -> dict[str, Any] | None:
        return next((item for item in self.items if item["status"] == "in_progress"), None)

    def mark_current_blocked(self) -> None:
        current = self.current()
        if current is not None:
            self.update(current["id"], "blocked")

    def render(self) -> str:
        if not self.items:
            return "[暂无任务清单]"
        return "\n".join(
            f"{MARKS[item['status']]} {item['id']} {item['text']}"
            for item in self.items
        )

    def all_done(self) -> bool:
        return bool(self.items) and all(item["status"] == "completed" for item in self.items)

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.items]
