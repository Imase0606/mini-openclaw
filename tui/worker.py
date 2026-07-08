"""后台 Worker：在线程内运行 AgentLoop 并发出事件。"""

from __future__ import annotations
import asyncio
import threading
import time
from typing import Any

from tools.base import ToolRegistry
from tui.state import (
    TokenEvent,
    ToolCallStartEvent,
    ToolCallEndEvent,
    StatusEvent,
    ErrorEvent,
    DoneEvent,
    TokenUsageEvent,
)


class AgentWorker:
    """在独立线程中运行 ReAct 循环，通过 asyncio.Queue 向 UI 发送事件。"""

    def __init__(
        self,
        task: str,
        backend: Any,
        registry: ToolRegistry,
        system_prompt: str,
        max_turns: int = 20,
    ) -> None:
        self.task = task
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns

        self._loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None

    def get_event_queue(self) -> asyncio.Queue:
        return self._event_queue

    def cancel(self) -> None:
        self._cancel_event.set()

    async def _emit(self, event: Any) -> None:
        """从工作线程向主线程的 asyncio 队列投递事件。"""
        self._loop.call_soon_threadsafe(self._event_queue.put_nowait, event)

    def run(self) -> None:
        """在工作线程中执行（供 Textual 的 run_worker 调用）。"""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.task},
        ]

        try:
            self._run_loop_sync(messages)
        except Exception as e:
            self._loop.call_soon_threadsafe(
                self._event_queue.put_nowait, ErrorEvent(message=str(e))
            )

    def _run_loop_sync(self, messages: list[dict]) -> None:
        """同步执行 ReAct 循环，边执行边发出事件。"""
        import json

        has_stream = hasattr(self.backend, "chat_stream")

        for turn in range(self.max_turns):
            if self._cancel_event.is_set():
                self._loop.call_soon_threadsafe(
                    self._event_queue.put_nowait,
                    StatusEvent(status="cancelled"),
                )
                return

            self._loop.call_soon_threadsafe(
                self._event_queue.put_nowait,
                StatusEvent(status="thinking"),
            )

            # ── 调用后端 ──
            if has_stream:
                content = ""
                tool_calls_raw = []
                for chunk in self.backend.chat_stream(messages, tools=self.registry.schemas()):
                    if self._cancel_event.is_set():
                        return
                    t = chunk.get("type")
                    if t == "content":
                        content += chunk["delta"]
                        self._loop.call_soon_threadsafe(
                            self._event_queue.put_nowait, TokenEvent(text=chunk["delta"])
                        )
                    elif t == "tool_call":
                        tool_calls_raw.append(chunk)
                    elif t == "usage":
                        self._loop.call_soon_threadsafe(
                            self._event_queue.put_nowait,
                            TokenUsageEvent(
                                prompt_tokens=chunk.get("prompt_tokens", 0),
                                completion_tokens=chunk.get("completion_tokens", 0),
                            ),
                        )
            else:
                resp = self.backend.chat(messages, tools=self.registry.schemas())
                content = resp.get("content", "")
                tool_calls_raw = resp.get("tool_calls", [])

            if not tool_calls_raw:
                # 最终回答
                self._loop.call_soon_threadsafe(
                    self._event_queue.put_nowait, DoneEvent(final_content=content)
                )
                self._loop.call_soon_threadsafe(
                    self._event_queue.put_nowait, StatusEvent(status="done")
                )
                return

            # ── 执行工具调用 ──
            for tc in tool_calls_raw:
                if self._cancel_event.is_set():
                    return

                name = tc["name"]
                args = tc.get("arguments", {})

                self._loop.call_soon_threadsafe(
                    self._event_queue.put_nowait,
                    ToolCallStartEvent(call_id=tc.get("id", ""), name=name, arguments=args),
                )

                self._loop.call_soon_threadsafe(
                    self._event_queue.put_nowait,
                    StatusEvent(status="executing"),
                )

                start = time.monotonic()
                tool = self.registry.get(name)
                if tool is None:
                    obs = f"错误：未知工具 {name}"
                    duration = (time.monotonic() - start) * 1000
                    self._loop.call_soon_threadsafe(
                        self._event_queue.put_nowait,
                        ToolCallEndEvent(call_id=tc.get("id", ""), result=obs, duration_ms=duration),
                    )
                else:
                    try:
                        obs = tool.run(**args)
                        duration = (time.monotonic() - start) * 1000
                        self._loop.call_soon_threadsafe(
                            self._event_queue.put_nowait,
                            ToolCallEndEvent(call_id=tc.get("id", ""), result=str(obs), duration_ms=duration),
                        )
                    except Exception as e:
                        duration = (time.monotonic() - start) * 1000
                        self._loop.call_soon_threadsafe(
                            self._event_queue.put_nowait,
                            ToolCallEndEvent(call_id=tc.get("id", ""), result=str(e), duration_ms=duration),
                        )

                # 注入观察结果到消息
                messages.append({
                    "role": "tool",
                    "name": name,
                    "tool_call_id": tc.get("id", ""),
                    "content": str(obs),
                })

        # 达到最大轮数
        self._loop.call_soon_threadsafe(
            self._event_queue.put_nowait,
            DoneEvent(final_content="[达到最大轮数上限，未完成任务]"),
        )
