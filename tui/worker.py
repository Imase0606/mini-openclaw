"""Thread bridge between AgentRuntime and the Textual event loop."""
from __future__ import annotations

import asyncio
import threading
import uuid

from agent.events import AgentEvent
from agent.runtime import AgentRuntime, RuntimeOptions
from tui.state import PermissionRequest


class AgentWorker:
    def __init__(
        self,
        runtime: AgentRuntime,
        task: str,
        options: RuntimeOptions,
        *,
        direct_tool: tuple[str, dict] | None = None,
    ) -> None:
        self.runtime = runtime
        self.task = task
        self.options = options
        self.direct_tool = direct_tool
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[AgentEvent | PermissionRequest] = asyncio.Queue()
        self.cancel_event = threading.Event()
        self.done_event = threading.Event()
        self._permissions: dict[str, PermissionRequest] = {}

    def run(self) -> None:
        self.runtime.event_sink = self._forward_event
        self.runtime.confirm_callback = self._request_permission
        status = "completed"
        try:
            if self.direct_tool:
                name, arguments = self.direct_tool
                result = self.runtime.execute_direct_tool(
                    name,
                    arguments,
                    permission_mode=self.options.permission_mode,
                )
                self._forward_event(AgentEvent("text_delta", {"text": result}))
                self._forward_event(AgentEvent("run_finished", {"content": result, "status": "completed", "turns": 0}))
            else:
                result = self.runtime.run_turn(self.task, options=self.options, cancel_event=self.cancel_event)
                status = result.status
        except Exception as exc:  # noqa: BLE001 - report backend failures to the UI
            status = "error"
            self._forward_event(AgentEvent("error", {"message": f"{type(exc).__name__}: {exc}"}))
        finally:
            self.done_event.set()
            self._forward_event(AgentEvent("worker_finished", {"status": status}))

    def cancel(self) -> None:
        self.cancel_event.set()
        for request in self._permissions.values():
            request.approved = False
            request.resolved.set()

    def resolve_permission(self, request_id: str, approved: bool) -> None:
        request = self._permissions.get(request_id)
        if request is None:
            return
        request.approved = approved
        request.resolved.set()

    def _forward_event(self, event: AgentEvent) -> None:
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
        except RuntimeError:
            pass

    def _request_permission(self, tool_name: str, arguments: dict) -> bool:
        request_id = uuid.uuid4().hex[:10]
        visible = {
            key: (str(value)[:500] if isinstance(value, str) else value)
            for key, value in arguments.items()
            if key in {"path", "command", "url", "timeout", "key"}
        }
        request = PermissionRequest(request_id, tool_name, visible, threading.Event())
        self._permissions[request_id] = request
        self.loop.call_soon_threadsafe(self.queue.put_nowait, request)
        while not request.resolved.wait(0.1):
            if self.cancel_event.is_set():
                request.approved = False
                break
        self._permissions.pop(request_id, None)
        return request.approved
