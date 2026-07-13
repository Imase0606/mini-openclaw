"""ReAct agent loop with permissions, planning, recovery and tracing."""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from agent.context import maybe_compact, truncate_observation
from agent.events import AgentEvent, RunResult
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
        auto_approve_tools: set[str] | None = None,
        confirm_callback: Callable[[str, dict[str, Any]], bool] | None = None,
        todo: TodoList | None = None,
        planning_mode: str = "auto",
        tracer: Tracer | None = None,
        event_sink: Callable[[AgentEvent], None] | None = None,
        cancel_event: threading.Event | None = None,
        run_id: str = "",
    ) -> None:
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max(1, min(int(max_turns), 100))
        self.tool_policy = tool_policy or ToolPolicy()
        self.auto_approve = auto_approve
        self.auto_approve_tools = set(auto_approve_tools or ())
        self.confirm_callback = confirm_callback
        self.todo = todo
        self.planning_mode = planning_mode
        self.tracer = tracer
        self.event_sink = event_sink
        self.cancel_event = cancel_event or threading.Event()
        self.run_id = run_id
        self.tool_schemas = self.tool_policy.schemas(self.registry)
        self._recent_actions: deque[str] = deque(maxlen=2)
        self._reflection_counts: dict[int, int] = {}
        self._terminal_success = False
        self.last_result: RunResult | None = None

    def run(self, user_task: str | list[dict[str, Any]]) -> str:
        return self.run_turn(user_task).content

    def run_turn(
        self,
        user_task: str | list[dict[str, Any]],
        history: list[dict[str, Any]] | None = None,
    ) -> RunResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *[
                dict(message) for message in (history or [])
                if message.get("role") in {"user", "assistant", "tool"} or message.get("_history_memo")
            ],
            {"role": "user", "content": user_task},
        ]
        stagnant_rounds = 0
        premature_finals = 0
        empty_responses = 0
        self._emit("status", status="thinking")

        for turn in range(1, self.max_turns + 1):
            if self.cancel_event.is_set():
                return self._finish(messages, "任务已取消。", "cancelled", turn - 1)
            self._emit("turn_started", turn=turn)
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
                result = str(assistant.get("content") or "")
                if not result.strip():
                    if empty_responses == 0:
                        empty_responses = 1
                        messages.append({
                            "role": "system",
                            "content": (
                                "上一次响应为空且没有工具调用。请继续当前任务：给出有效答复，"
                                "或调用完成任务所需的工具；不要再次返回空响应。"
                            ),
                        })
                        continue
                    result = "模型连续返回空响应，任务已停止。请重试或切换模型。"
                    self._record_run_end("empty_response", result, turn)
                    return self._finish(messages, result, "empty_response", turn)
                empty_responses = 0
                if self._must_continue_planning() and premature_finals < 2:
                    premature_finals += 1
                    messages.append({
                        "role": "system",
                        "content": "规划尚未完成，不能直接结束。请列出或推进 Todo；无法继续则标记 blocked 并汇报原因。",
                    })
                    continue
                self._record_run_end("completed", result, turn)
                return self._finish(messages, result, "completed", turn)

            empty_responses = 0

            version_before = self.todo.version if self.todo is not None else 0
            reflection_messages: list[str] = []
            for call in tool_calls:
                if self.cancel_event.is_set():
                    return self._finish(messages, "任务已取消。", "cancelled", turn)
                call_name = call.get("name") or ""
                raw_arguments = call.get("arguments", {})
                arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
                arguments_error = call.get("arguments_error")
                if not isinstance(raw_arguments, dict) and not arguments_error:
                    arguments_error = {
                        "type": "TypeError",
                        "message": "工具参数必须是 JSON 对象",
                        "length": 0,
                    }
                call_id = call.get("id") or f"call-{turn}-{len(messages)}"
                self._emit(
                    "tool_started",
                    call_id=call_id,
                    name=call_name,
                    arguments=self._event_arguments(arguments),
                )
                tool_started = time.perf_counter()
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
                    obs, failed = self._execute_tool(
                        call_name,
                        arguments,
                        arguments_error=arguments_error,
                    )

                outcome = "denied" if str(obs).startswith("[权限层]") else "error" if failed else "done"
                self._emit(
                    "tool_finished",
                    call_id=call_id,
                    name=call_name,
                    status=outcome,
                    result=str(obs)[:4000],
                    duration_ms=round((time.perf_counter() - tool_started) * 1000),
                )
                self._emit("status", status="thinking")
                self._emit_artifacts(call_name, str(obs))

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
                    self._emit("todo_changed", items=self.todo.snapshot(), rendered=self.todo.render())
                if stagnant_rounds == 4:
                    messages.append({
                        "role": "system",
                        "content": "[规划层] Todo 已连续 4 轮无推进。请更新状态、插入恢复步骤或标记 blocked。",
                    })
                if stagnant_rounds >= 8:
                    self.todo.mark_current_blocked()
                    result = "[规划层] 连续 8 轮无 Todo 进展，已停止。\n" + self.todo.render()
                    self._record_run_end("no_progress", result, turn)
                    return self._finish(messages, result, "no_progress", turn)

            messages = maybe_compact(messages, backend=self.backend, budget=6000)

        progress = "\n" + self.todo.render() if self.todo is not None and self.todo.items else ""
        result = f"[达到最大轮数上限（{self.max_turns}/{self.max_turns} 轮），未完成任务]{progress}"
        self._record_run_end("max_turns", result, self.max_turns)
        return self._finish(messages, result, "max_turns", self.max_turns)

    def _call_llm(self, messages: list[dict[str, Any]], turn: int, prefix_chars: int) -> dict[str, Any]:
        if self.event_sink is not None and hasattr(self.backend, "chat_stream"):
            return self._call_llm_stream(messages, turn, prefix_chars)
        fn = lambda: self.backend.chat(messages, tools=self.tool_schemas)
        if self.tracer is None:
            result = fn()
        else:
            result = self.tracer.call(
            "llm",
            "decide",
            fn,
            input_data={"turn": turn, "messages": len(messages), "tools": len(self.tool_schemas)},
            meta={"turn": turn, "prefix_chars": prefix_chars, "run_id": self.run_id},
        )
        content = result.get("content", "")
        if content:
            self._emit("text_delta", text=content)
        self._emit("usage", **(result.get("usage") or {}))
        return result

    def _call_llm_stream(
        self,
        messages: list[dict[str, Any]],
        turn: int,
        prefix_chars: int,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        content = ""
        tool_calls: list[dict[str, Any]] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        ok = True
        error = ""
        try:
            for chunk in self.backend.chat_stream(messages, tools=self.tool_schemas):
                if self.cancel_event.is_set():
                    break
                kind = chunk.get("type")
                if kind == "content":
                    delta = str(chunk.get("delta") or "")
                    content += delta
                    self._emit("text_delta", text=delta)
                elif kind == "tool_call":
                    call = {
                        "id": chunk.get("id"),
                        "name": chunk.get("name"),
                        "arguments": chunk.get("arguments") or {},
                    }
                    if chunk.get("arguments_error"):
                        call["arguments_error"] = chunk["arguments_error"]
                    tool_calls.append(call)
                elif kind == "usage":
                    usage = {
                        "prompt_tokens": int(chunk.get("prompt_tokens") or 0),
                        "completion_tokens": int(chunk.get("completion_tokens") or 0),
                        "total_tokens": int(chunk.get("total_tokens") or 0),
                    }
                    self._emit("usage", **usage)
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if self.tracer:
                self.tracer.record(
                    "llm",
                    "decide",
                    ok=ok,
                    ms=round((time.perf_counter() - started) * 1000),
                    input_data={"turn": turn, "messages": len(messages), "tools": len(self.tool_schemas)},
                    output={"content": content, "tool_calls": tool_calls} if ok else error,
                    usage=usage,
                    meta={"turn": turn, "prefix_chars": prefix_chars, "run_id": self.run_id},
                )
        return {"role": "assistant", "content": content, "tool_calls": tool_calls, "usage": usage}

    def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        arguments_error: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        self._emit("status", status="executing", tool=name)
        tool = self.registry.get(name)
        parameter_error = self._parameter_error(name, arguments, arguments_error)
        if parameter_error:
            if self.tracer:
                self.tracer.record(
                    "parameters",
                    name,
                    ok=False,
                    input_data=arguments_error or {"missing": self._missing_required(name, arguments)},
                    output=parameter_error,
                )
            return parameter_error, True
        verdict, reason = self.tool_policy.authorize(name, arguments)
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
                self.tool_policy.observe(name, str(obs))
                self._emit("status", status="thinking")
                return wrapped, failed
            except Exception as exc:
                transient = self._is_transient(exc)
                if transient and attempt < max_attempts:
                    time.sleep(0.25 * (2 ** (attempt - 1)))
                    continue
                return f"工具 {name} 执行出错：{type(exc).__name__}: {exc}", True
        return f"工具 {name} 执行失败", True

    def _parameter_error(
        self,
        name: str,
        arguments: dict[str, Any],
        arguments_error: dict[str, Any] | None,
    ) -> str:
        if arguments_error:
            error_type = str(arguments_error.get("type") or "JSONDecodeError")
            position = arguments_error.get("position")
            length = int(arguments_error.get("length") or 0)
            if error_type == "OutputTruncated":
                return (
                    f"[参数层] 工具 {name} 参数因模型输出长度上限被截断，"
                    f"已接收参数长度 {length}。请缩短内容后重新生成完整参数。"
                )
            location = f"，错误位置 {position}" if position is not None else ""
            return (
                f"[参数层] 工具 {name} 参数 JSON 解析失败：{error_type}"
                f"{location}，参数长度 {length}。请重新生成完整的 JSON 对象参数。"
            )
        missing = self._missing_required(name, arguments)
        if missing:
            return f"[参数层] 工具 {name} 缺少必需参数：{', '.join(missing)}。请补齐后重试。"
        return ""

    def _missing_required(self, name: str, arguments: dict[str, Any]) -> list[str]:
        for schema in self.tool_schemas:
            function = schema.get("function") or {}
            if function.get("name") != name:
                continue
            parameters = function.get("parameters") or {}
            required = parameters.get("required") or []
            return [
                str(key) for key in required
                if key not in arguments or arguments.get(key) is None or arguments.get(key) == ""
            ]
        return []

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
        if self.auto_approve or name in self.auto_approve_tools:
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
                    "run_id": self.run_id,
                    "todo": self.todo.snapshot() if self.todo is not None else [],
                },
            )

    def _finish(
        self,
        messages: list[dict[str, Any]],
        content: str,
        status: str,
        turns: int,
    ) -> RunResult:
        history = [
            dict(message) for message in messages[1:]
            if message.get("role") != "system" or message.get("_history_memo")
        ]
        result = RunResult(content=content, messages=history, status=status, turns=turns)
        self.last_result = result
        self._emit("status", status="done" if status == "completed" else status)
        self._emit("run_finished", content=content, status=status, turns=turns)
        return result

    def _emit(self, kind: str, **data: Any) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(AgentEvent(kind, {"run_id": self.run_id, **data}))
        except Exception:
            pass

    @staticmethod
    def _event_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        visible = {}
        for key, value in arguments.items():
            if key.lower() in {"api_key", "token", "password", "secret", "data", "image"}:
                visible[key] = "[REDACTED]"
            elif isinstance(value, str) and len(value) > 500:
                visible[key] = value[:500] + "..."
            else:
                visible[key] = value
        return visible

    def _emit_artifacts(self, tool_name: str, observation: str) -> None:
        if tool_name not in {"kb_write", "video_frame_ocr"}:
            return
        raw = observation
        if raw.startswith("<external "):
            start = raw.find("{")
            end = raw.rfind("}")
            raw = raw[start:end + 1] if start >= 0 and end >= start else ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        keys = (
            ("visual_notes_path", "contact_sheet_path")
            if tool_name == "video_frame_ocr"
            else ("markdown_path", "metadata_path", "transcript_path", "visual_notes_path", "chunks_path")
        )
        for key in keys:
            path = payload.get(key)
            if path:
                self._emit("artifact", kind_name=key, path=str(path))
