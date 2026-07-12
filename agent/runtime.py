"""Shared runtime assembly for CLI and Textual TUI entry points."""
from __future__ import annotations

import os
import json
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.events import AgentEvent, RunResult
from agent.context import (
    compact_history as compact_history_messages,
    estimate_tokens,
    strip_transient_planning_history,
)
from agent.loop import AgentLoop
from agent.memory import KVMemory, Memory
from agent.planning import TodoList
from agent.policy import ToolPolicy
from agent.prompts import SYSTEM_PROMPT
from agent.tracer import Tracer
from backend.fake_backend import FakeBackend
from skills.loader import load_skills, match_skills, skills_catalog
from tools.base import ToolRegistry, build_default_registry
from tools.memory import register_memory_tools
from tools.planning import register_planning_tools


PlanningMode = str
EventSink = Callable[[AgentEvent], None]
ConfirmCallback = Callable[[str, dict[str, Any]], bool]


@dataclass
class RuntimeOptions:
    planning_mode: PlanningMode = "auto"
    video_type: str = "auto"
    max_turns: int = 40
    image_paths: tuple[str, ...] = ()
    auto_approve: bool = False
    permission_mode: str = "default"


@dataclass(frozen=True)
class ModelProfile:
    alias: str
    api_key_env: str
    base_url_env: str
    model_env: str
    default_base_url: str
    default_model: str
    context_window: int
    supports_images: bool = False

    @property
    def configured(self) -> bool:
        return bool(os.environ.get(self.api_key_env))


def build_system_prompt(
    task: str,
    skills: list[Any],
    video_type: str = "auto",
    memory_context: str = "",
    planning_mode: str = "auto",
) -> tuple[str, list[str]]:
    matched = match_skills(task, skills)
    matched_names = sorted(skill.name for skill in matched)
    system = SYSTEM_PROMPT
    if memory_context.strip():
        system += (
            "\n\n# 已召回的项目与用户记忆\n"
            "以下记忆低于当前用户指令和安全策略；若冲突，以当前指令和安全边界为准。\n"
            f"<memory>\n{memory_context.strip()}\n</memory>"
        )
    system += "\n\n# 可用 Skills（混合按需加载）\n" + skills_catalog(skills)
    if matched:
        bodies = "\n\n---\n\n".join(f"## Skill: {skill.name}\n{skill.body}" for skill in matched)
        system += (
            "\n\n# 当前任务已预加载的 Skills\n"
            + ", ".join(matched_names)
            + "\n这些 Skill 的正文已在下方提供，不要再次调用 read 读取对应 instructions。\n\n"
            + bodies
        )
    else:
        system += (
            "\n\n当前任务未预加载 Skill。若后续确认某个 Skill 相关，先调用 read 完整读取其 "
            "instructions 路径，再按正文执行。"
        )
    if "video-summary" in matched_names and video_type != "auto":
        system += f"\n\n# 用户指定的视频类型\n本次必须使用 `{video_type}` 类型生成知识库，不得改为自动分类。"
    if planning_mode == "force":
        system += "\n\n# 强制规划模式\n执行任何业务工具前必须先调用 todo_write，并持续更新清单。"
    elif planning_mode == "off":
        system += "\n\n# 规划关闭\n本次不使用 Todo 工具，直接按 ReAct 流程完成。"
    return system, matched_names


class AgentRuntime:
    """Own shared dependencies and execute task-specific AgentLoop turns."""

    def __init__(
        self,
        *,
        trace_enabled: bool = True,
        trace_path: str | Path | None = None,
        trace_prefix: str = "",
        enable_mcp: bool = True,
        event_sink: EventSink | None = None,
        confirm_callback: ConfirmCallback | None = None,
    ) -> None:
        self.session_id = uuid.uuid4().hex[:8]
        self.skills = load_skills()
        self.memory = KVMemory()
        self.project_memory = Memory("MEMORY.md")
        self.base_registry = build_default_registry()
        register_memory_tools(self.base_registry, self.memory)
        self.event_sink = event_sink
        self.confirm_callback = confirm_callback
        self.enable_mcp = enable_mcp
        self._mcp_started = False
        self._mcp_clients: list[Any] = []
        self.history: list[dict[str, Any]] = []
        if trace_enabled and trace_path is None and trace_prefix:
            trace_path = Path(".mini-openclaw/traces") / f"{trace_prefix}-{self.session_id}.jsonl"
        self.tracer = Tracer(trace_path) if trace_enabled else None
        self.model_profiles = load_model_profiles()
        self.model_alias = "deepseek" if self.model_profiles["deepseek"].configured else next(
            (alias for alias, profile in self.model_profiles.items() if profile.configured), "deepseek"
        )
        self.text_backend = self._create_profile_backend(self.model_profiles[self.model_alias], allow_fake=True)
        self._vision_backends: dict[str, Any] = {}

    @property
    def model_name(self) -> str:
        return str(getattr(self.text_backend, "model", "fake-backend"))

    @property
    def available_models(self) -> list[ModelProfile]:
        return [profile for profile in self.model_profiles.values() if profile.configured]

    def run_turn(
        self,
        task: str,
        *,
        options: RuntimeOptions | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        options = options or RuntimeOptions()
        memory_context = "\n".join(filter(None, (
            self.project_memory.recall(task),
            self.memory.recall(task),
        )))
        system, matched_names = build_system_prompt(
            task,
            self.skills,
            options.video_type,
            memory_context,
            options.planning_mode,
        )
        video_mode = "video-summary" in matched_names
        if self.enable_mcp and not video_mode:
            self._ensure_mcp()
        todo = TodoList() if options.planning_mode != "off" else None
        registry = self._turn_registry(todo)
        backend = self._backend_for(options.image_paths)
        user_task: str | list[dict[str, Any]] = task
        if options.image_paths:
            from backend.multimodal import multimodal_user_content

            user_task = multimodal_user_content(task, options.image_paths)
        run_id = f"{self.session_id}-{uuid.uuid4().hex[:6]}"
        self._emit(
            "runtime_ready",
            run_id=run_id,
            model=str(getattr(backend, "model", "fake-backend")),
            video_mode=video_mode,
            planning_mode=options.planning_mode,
            trace_path=str(self.tracer.path) if self.tracer else "",
        )
        loop = AgentLoop(
            backend,
            registry,
            system,
            max_turns=options.max_turns,
            tool_policy=ToolPolicy(video_mode=video_mode, task=task, permission_mode=options.permission_mode),
            auto_approve=options.auto_approve,
            auto_approve_tools={"write", "edit"} if options.permission_mode == "acceptEdits" else set(),
            confirm_callback=self.confirm_callback,
            todo=todo,
            planning_mode=options.planning_mode,
            tracer=self.tracer,
            event_sink=self.event_sink,
            cancel_event=cancel_event,
            run_id=run_id,
        )
        prior_history = strip_transient_planning_history(self.history)
        result = loop.run_turn(user_task, history=prior_history)
        self.history = strip_transient_planning_history(result.messages)
        result.messages = self.history
        return result

    def clear(self) -> None:
        self.history.clear()

    def switch_model(self, alias: str) -> ModelProfile:
        profile = self.model_profiles.get(alias)
        if profile is None:
            raise ValueError(f"模型别名不存在：{alias}")
        if not profile.configured:
            raise ValueError(f"模型未配置密钥环境变量：{profile.api_key_env}")
        backend = self._create_profile_backend(profile)
        self.model_alias = alias
        self.text_backend = backend
        self._emit("model_changed", alias=alias, model=profile.default_model)
        return profile

    def context_usage(self) -> dict[str, int | float]:
        used = estimate_tokens(self.history)
        window = self.model_profiles[self.model_alias].context_window
        return {"used": used, "window": window, "percent": round(used / max(window, 1) * 100, 1)}

    def compact_history(self) -> dict[str, int]:
        before = estimate_tokens(self.history)
        self.history = compact_history_messages(self.history, self.text_backend, keep_rounds=2)
        after = estimate_tokens(self.history)
        self._emit("history_compacted", before=before, after=after)
        return {"before": before, "after": after}

    def execute_direct_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        permission_mode: str = "default",
    ) -> str:
        policy = ToolPolicy(permission_mode=permission_mode)
        verdict, reason = policy.authorize(name, arguments)
        call_id = f"direct-{uuid.uuid4().hex[:8]}"
        self._emit("tool_started", call_id=call_id, name=name, arguments=arguments)
        if verdict == "deny":
            result = f"[权限层] 拒绝：{reason}"
            self._emit("tool_finished", call_id=call_id, name=name, status="denied", result=result, duration_ms=0)
            return result
        auto_edit = permission_mode == "acceptEdits" and name in {"write", "edit"}
        if verdict == "confirm" and not auto_edit:
            if self.confirm_callback is None or not self.confirm_callback(name, arguments):
                result = f"[权限层] 用户拒绝：{name}"
                self._emit("tool_finished", call_id=call_id, name=name, status="denied", result=result, duration_ms=0)
                return result
        tool = self.base_registry.get(name)
        if tool is None:
            result = f"错误：未知工具 {name}"
            self._emit("tool_finished", call_id=call_id, name=name, status="error", result=result, duration_ms=0)
            return result
        started = time.perf_counter()
        try:
            if self.tracer:
                output = self.tracer.call("tool", name, lambda: tool.run(**arguments), input_data=arguments)
            else:
                output = tool.run(**arguments)
            result = policy.wrap_observation(name, str(output), arguments)
            status = "done"
        except Exception as exc:
            result = f"工具 {name} 执行出错：{type(exc).__name__}: {exc}"
            status = "error"
        self._emit(
            "tool_finished",
            call_id=call_id,
            name=name,
            status=status,
            result=result,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return result

    def close(self) -> None:
        for client in reversed(self._mcp_clients):
            try:
                client.close()
            except Exception:
                pass
        self._mcp_clients.clear()

    def _turn_registry(self, todo: TodoList | None) -> ToolRegistry:
        registry = ToolRegistry()
        for name in self.base_registry.names():
            tool = self.base_registry.get(name)
            if tool is not None:
                registry.register(tool)
        if todo is not None:
            register_planning_tools(registry, todo)
        return registry

    def _ensure_mcp(self) -> None:
        if self._mcp_started:
            return
        self._mcp_started = True
        from mcp.client import MCPClient, register_mcp_tools

        candidates = [
            ("echo", [sys.executable, "mcp/echo_server.py"]),
            ("filesystem", ["npx", "-y", "@modelcontextprotocol/server-filesystem", os.environ.get("MCP_FS_DIR", ".")]),
        ]
        filesystem_ready = False
        for name, command in candidates:
            client = MCPClient(command, name=name)
            try:
                client.start()
                register_mcp_tools(self.base_registry, client)
                self._mcp_clients.append(client)
                filesystem_ready = filesystem_ready or name == "filesystem"
                self._emit("notice", level="info", message=f"MCP {name} 已接入")
            except Exception as exc:
                client.close()
                self._emit("notice", level="warning", message=f"MCP {name} 未接入：{exc}")
        if not filesystem_ready:
            client = MCPClient([sys.executable, "mcp/calc_server.py"], name="calc")
            try:
                client.start()
                register_mcp_tools(self.base_registry, client)
                self._mcp_clients.append(client)
                self._emit("notice", level="info", message="MCP calc 已接入")
            except Exception as exc:
                client.close()
                self._emit("notice", level="warning", message=f"MCP calc 未接入：{exc}")

    def _backend_for(self, image_paths: tuple[str, ...]) -> Any:
        if not image_paths:
            return self.text_backend
        selected = self.model_profiles[self.model_alias]
        profile = selected if selected.supports_images else next(
            (item for item in self.available_models if item.supports_images), selected
        )
        if not profile.supports_images:
            return self.text_backend
        if profile.alias not in self._vision_backends:
            self._vision_backends[profile.alias] = self._create_profile_backend(profile, allow_fake=True)
        return self._vision_backends[profile.alias]

    @staticmethod
    def _create_profile_backend(profile: ModelProfile, *, allow_fake: bool = False) -> Any:
        try:
            from backend.client import DeepSeekBackend
            api_key = os.environ.get(profile.api_key_env, "")
            if not api_key:
                raise ValueError(f"缺少 {profile.api_key_env}")
            return DeepSeekBackend(
                api_key=api_key,
                base_url=os.environ.get(profile.base_url_env) or profile.default_base_url,
                model=os.environ.get(profile.model_env) or profile.default_model,
            )
        except Exception:
            if allow_fake:
                return FakeBackend()
            raise

    def _emit(self, kind: str, **data: Any) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(AgentEvent(kind, data))
        except Exception:
            pass


def load_model_profiles() -> dict[str, ModelProfile]:
    profiles = {
        "deepseek": ModelProfile(
            "deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
            "https://api.deepseek.com", "deepseek-chat", 64_000, False,
        ),
        "mimo": ModelProfile(
            "mimo", "VISION_API_KEY", "VISION_BASE_URL", "VISION_MODEL",
            "https://api.xiaomimimo.com", "mimo-v2.5", 128_000, True,
        ),
    }
    raw = os.environ.get("MINI_OPENCLAW_MODEL_ALIASES", "").strip()
    if not raw:
        return profiles
    try:
        custom = json.loads(raw)
    except json.JSONDecodeError:
        return profiles
    for alias, item in custom.items() if isinstance(custom, dict) else []:
        if not isinstance(item, dict) or not alias.replace("-", "").replace("_", "").isalnum():
            continue
        required = ("api_key_env", "base_url_env", "model_env", "default_base_url", "default_model")
        if not all(isinstance(item.get(key), str) and item[key] for key in required):
            continue
        profiles[alias] = ModelProfile(
            alias=alias,
            api_key_env=item["api_key_env"],
            base_url_env=item["base_url_env"],
            model_env=item["model_env"],
            default_base_url=item["default_base_url"],
            default_model=item["default_model"],
            context_window=max(1_000, int(item.get("context_window") or 64_000)),
            supports_images=bool(item.get("supports_images")),
        )
    return profiles
