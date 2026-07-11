"""ReAct agent loop with permissions, planning, recovery and tracing."""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from agent.context import maybe_compact, truncate_observation
from agent.planning import TodoList
from agent.policy import ToolPolicy
from agent.tracer import Tracer
from tools.base import ToolRegistry


IDEMPOTENT_RETRY_TOOLS = {
    "read", "grep", "glob", "web_fetch", "recall_memory", "video_probe",
}
FAILURE_MARKERS = ("[错误]", "[失败]", "[超时]", "执行出错", "returncode=")


class AgentLoop:
    def __init__(
        self,
        backend: Any,
        registry: ToolRegistry,
        system_prompt: str,
        max_turns: int = 40,
        tool_policy: ToolPolicy | None = None,
        auto_approve: bool = False,
        confirm_callback: Callable[[str, dict[str, Any]], bool] | None = None,
        todo: TodoList | None = None,
        planning_mode: str = "auto",
        tracer: Tracer | None = None,
    ) -> None:
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max(1, min(int(max_turns), 100))
        self.tool_policy = tool_policy or ToolPolicy()
        self.auto_approve = auto_approve
        self.confirm_callback = confirm_callback
        self.todo = todo
        self.planning_mode = planning_mode
        self.tracer = tracer
        self.tool_schemas = self.tool_policy.schemas(self.registry)
        self._recent_actions: deque[str] = deque(maxlen=2)
        self._reflection_counts: dict[int, int] = {}
        self._terminal_success = False

    def run(self, user_task: str | list[dict[str, Any]]) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_task},
        ]
        stagnant_rounds = 0
        premature_finals = 0

        for turn in range(1, self.max_turns + 1):
            request_messages = list(messages)
            if self.todo is not None and self.todo.items:
                request_messages.append({
                    "role": "system",
                    "content": "# 当前任务清单\n推进清单并及时更新状态，不要重复已完成项。\n" + self.todo.render(),
                })
            elif self.planning_mode == "force":
                request_messages.append({
                    "role": "system",
                    "content": "# 强制规划\n当前任务必须先调用 todo_write 分解，再执行其它工具。",
                })

            prefix_chars = self.tracer.observe_request(request_messages, self.tool_schemas) if self.tracer else 0
            assistant = self._call_llm(request_messages, turn, prefix_chars)
            messages.append({
                "role": "assistant",
                "content": assistant.get("content", ""),
                "tool_calls": assistant.get("tool_calls", []),
            })

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                if self._must_continue_planning() and premature_finals < 2:
                    premature_finals += 1
                    messages.append({
                        "role": "system",
                        "content": "规划尚未完成，不能直接结束。请列出或推进 Todo；无法继续则标记 blocked 并汇报原因。",
                    })
                    continue
                result = assistant.get("content", "")
                self._record_run_end("completed", result, turn)
                return result

            version_before = self.todo.version if self.todo is not None else 0
            reflection_messages: list[str] = []
            for call in tool_calls:
                call_name = call.get("name") or ""
                arguments = call.get("arguments", {})
                signature = json.dumps(
                    {"name": call_name, "arguments": arguments},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                missing_required_plan = (
                    self.planning_mode == "force"
                    and self.todo is not None
                    and not self.todo.items
                    and call_name != "todo_write"
                )
                after_terminal_success = (
                    self._terminal_success
                    and self.todo is not None
                    and self.todo.all_done()
                    and call_name not in {"todo_write", "update_todo", "insert_todo"}
                )
                repeated = len(self._recent_actions) == 2 and all(item == signature for item in self._recent_actions)
                self._recent_actions.append(signature)
                if after_terminal_success:
                    obs = "[规划层] 终态产物已成功生成且 Todo 全部完成；拒绝重复业务工具，请直接给出最终答复。"
                    failed = True
                    if self.tracer:
                        self.tracer.record("planning", "terminal_guard", ok=False, output=obs)
                elif missing_required_plan:
                    obs = "[规划层] 强制规划模式下必须先调用 todo_write，业务工具尚未执行。"
                    failed = True
                    if self.tracer:
                        self.tracer.record("planning", "plan_required", ok=False, output=obs)
                elif repeated:
                    if self.todo is not None:
                        self.todo.mark_current_blocked()
                    obs = "[规划层] 检测到同一工具和参数连续出现 3 次，已阻断并要求重规划。"
                    failed = True
                    if self.tracer:
                        self.tracer.record("planning", "repeat_guard", ok=False, output=obs)
                else:
                    obs, failed = self._execute_tool(call_name, arguments)

                read_name = Path(str(arguments.get("path", ""))).name.lower()
                observation_limit = (
                    80_000
                    if call_name == "read" and read_name.startswith("transcript") and read_name.endswith(".txt")
                    else 4_000
                )
                messages.append({
                    "role": "tool",
                    "name": call_name,
                    "tool_call_id": call.get("id"),
                    "content": truncate_observation(str(obs), max_chars=observation_limit),
                })
                if failed:
                    reflection = self._reflection_prompt(call_name, str(obs))
                    if reflection:
                        reflection_messages.append(reflection)

            for reflection in reflection_messages:
                messages.append({"role": "system", "content": reflection})

            if self._terminal_success and self.todo is not None and self.todo.all_done():
                messages.append({
                    "role": "system",
                    "content": "[规划层] 终态产物已经成功生成，Todo 全部完成。不要再调用工具，直接总结结果和输出路径。",
                })

            if self.todo is not None and self.todo.items:
                if self.todo.version == version_before:
                    stagnant_rounds += 1
                else:
                    stagnant_rounds = 0
                if stagnant_rounds == 4:
                    messages.append({
                        "role": "system",
                        "content": "[规划层] Todo 已连续 4 轮无推进。请更新状态、插入恢复步骤或标记 blocked。",
                    })
                if stagnant_rounds >= 8:
                    self.todo.mark_current_blocked()
                    result = "[规划层] 连续 8 轮无 Todo 进展，已停止。\n" + self.todo.render()
                    self._record_run_end("no_progress", result, turn)
                    return result

            messages = maybe_compact(messages, backend=self.backend, budget=6000)

        progress = "\n" + self.todo.render() if self.todo is not None and self.todo.items else ""
        result = f"[达到最大轮数上限（{self.max_turns}/{self.max_turns} 轮），未完成任务]{progress}"
        self._record_run_end("max_turns", result, self.max_turns)
        return result

    def _call_llm(self, messages: list[dict[str, Any]], turn: int, prefix_chars: int) -> dict[str, Any]:
        fn = lambda: self.backend.chat(messages, tools=self.tool_schemas)
        if self.tracer is None:
            return fn()
        return self.tracer.call(
            "llm",
            "decide",
            fn,
            input_data={"turn": turn, "messages": len(messages), "tools": len(self.tool_schemas)},
            meta={"turn": turn, "prefix_chars": prefix_chars},
        )

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        verdict, reason = self.tool_policy.authorize(name, arguments)
        tool = self.registry.get(name)
        if verdict == "deny":
            obs = f"[权限层] 拒绝：{reason}"
            if self.tracer:
                self.tracer.record("policy", name, ok=False, input_data=arguments, output=obs)
            return self.tool_policy.wrap_observation(name, obs, arguments), True
        if verdict == "confirm" and not self._confirmed(name, arguments):
            obs = f"[权限层] 需确认：{name}({self._safe_arguments(arguments)})，已拦截"
            if self.tracer:
                self.tracer.record("policy", name, ok=False, input_data=arguments, output=obs)
            return self.tool_policy.wrap_observation(name, obs, arguments), True
        if tool is None:
            return f"错误：未知工具 {name}", True

        max_attempts = 3 if name in IDEMPOTENT_RETRY_TOOLS else 1
        for attempt in range(1, max_attempts + 1):
            try:
                fn = lambda: tool.run(**arguments)
                if self.tracer:
                    obs = self.tracer.call(
                        "tool",
                        name,
                        fn,
                        input_data=arguments,
                        meta={"attempt": attempt, "todo_version": self.todo.version if self.todo else 0},
                    )
                else:
                    obs = fn()
                wrapped = self.tool_policy.wrap_observation(name, str(obs), arguments)
                failed = any(marker in str(obs) for marker in FAILURE_MARKERS)
                if not failed and name == "kb_write":
                    self._terminal_success = True
                return wrapped, failed
            except Exception as exc:
                transient = self._is_transient(exc)
                if transient and attempt < max_attempts:
                    time.sleep(0.25 * (2 ** (attempt - 1)))
                    continue
                return f"工具 {name} 执行出错：{type(exc).__name__}: {exc}", True
        return f"工具 {name} 执行失败", True

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        return isinstance(exc, (TimeoutError, ConnectionError)) or any(
            marker in name for marker in ("timeout", "connect", "network", "tempor")
        )

    def _must_continue_planning(self) -> bool:
        if self.planning_mode != "force" or self.todo is None:
            return False
        if not self.todo.items:
            return True
        unfinished = [item for item in self.todo.items if item["status"] in {"pending", "in_progress"}]
        return bool(unfinished)

    def _reflection_prompt(self, tool_name: str, observation: str) -> str:
        item_id = 0
        if self.todo is not None and self.todo.current() is not None:
            item_id = int(self.todo.current()["id"])
        count = self._reflection_counts.get(item_id, 0)
        if count >= 2:
            if self.todo is not None:
                self.todo.mark_current_blocked()
            return ""
        self._reflection_counts[item_id] = count + 1
        return (
            f"[反思 {count + 1}/2] 工具 {tool_name} 失败：{observation[:500]}\n"
            "判断是参数错误、瞬时失败还是永久限制；修正、重规划或把当前 Todo 标记 blocked。"
        )

    def _confirmed(self, name: str, arguments: dict[str, Any]) -> bool:
        if self.auto_approve:
            return True
        if self.confirm_callback is None:
            return False
        try:
            return bool(self.confirm_callback(name, arguments))
        except (EOFError, KeyboardInterrupt):
            return False

    @staticmethod
    def _safe_arguments(arguments: dict[str, Any]) -> str:
        visible = {
            key: value for key, value in arguments.items()
            if key in {"path", "command", "url", "timeout", "key"}
        }
        if "command" in visible:
            visible["command"] = str(visible["command"])[:200]
        return str(visible)

    def _record_run_end(self, status: str, output: str, turn: int) -> None:
        if self.tracer:
            self.tracer.record(
                "run",
                status,
                ok=status == "completed",
                output=output,
                meta={
                    "turn": turn,
                    "todo": self.todo.snapshot() if self.todo is not None else [],
                },
            )
