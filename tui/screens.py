"""Claude Code inspired main screen, commands and permission flows."""
from __future__ import annotations

import asyncio
import json
import shlex
import threading
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Label, OptionList, Static, TextArea

from agent.events import AgentEvent
from agent.runtime import AgentRuntime, RuntimeOptions
from agent.session import SessionStore
from agent.tracer import redact_text
from backend.multimodal import image_block
from tui.artifact_preview import ArtifactPreviewModal
from tui.chat_view import AssistantMessage, ChatContainer, WelcomePanel
from tui.composer import Composer
from tui.completion import (
    COMMANDS,
    CompletionMenu,
    command_suggestions,
    file_query,
    file_suggestions,
    replace_completion,
    workspace_files,
)
from tui.file_link import open_artifact, resolve_artifact
from tui.input_area import PromptInput
from tui.modals import BilibiliLoginModal, ChoiceModal
from tui.sidebar import SidePanel
from tui.state import ArtifactRecord, PermissionRequest, QueuedRequest, TUISettings
from tui.widgets import ToolCallCard
from tui.worker import AgentWorker


HELP_TEXT = "\n".join(f"{command:<18} {description}" for command, description in COMMANDS.items())
PERMISSION_MODES = ("default", "acceptEdits", "plan")


class PermissionModal(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "approve", "Allow"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Deny"),
    ]

    def __init__(self, tool_name: str, arguments: dict) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments
        self.border_title = " Permission required "

    def compose(self) -> ComposeResult:
        with Vertical(id="permission-dialog"):
            yield Label("Review tool request", id="permission-title")
            yield Label(f"Tool: {self.tool_name}")
            yield Static(
                json.dumps(self.arguments, ensure_ascii=False, indent=2),
                id="permission-arguments",
                markup=False,
            )
            with Horizontal(id="permission-actions"):
                yield Button("Allow once", variant="primary", id="permission-allow")
                yield Button("Deny", variant="error", id="permission-deny")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "permission-allow")


class MainScreen(Screen):
    BINDINGS = [
        Binding("ctrl+c", "cancel", "Interrupt"),
        Binding("ctrl+d", "quit", "Quit"),
        Binding("shift+tab", "cycle_permission", "Permission mode"),
        Binding("ctrl+b", "toggle_drawer", "Details"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, runtime_factory: Callable[[], AgentRuntime] | None = None) -> None:
        super().__init__()
        self.runtime_factory = runtime_factory or AgentRuntime
        self.runtime: AgentRuntime | None = None
        self.session_store: SessionStore | None = None
        self.session_created_at = ""
        self.worker: AgentWorker | None = None
        self.worker_thread: threading.Thread | None = None
        self.poller: asyncio.Task | None = None
        self.current_message: AssistantMessage | None = None
        self.current_request: QueuedRequest | None = None
        self.tool_cards: dict[str, ToolCallCard] = {}
        self.artifacts: list[ArtifactRecord] = []
        self.pending_images: list[str] = []
        self.request_queue: deque[QueuedRequest] = deque(maxlen=10)
        self.settings = TUISettings()
        self.busy = False
        self.drawer_open = False
        self.prompt_tokens = 0
        self.completion_files: list[str] = []
        self.activity_status = "idle"
        self.activity_tool = ""
        self.activity_turn = 0
        self.pending_turn_message = False
        self.bilibili_login_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="workspace"):
            yield ChatContainer()
            yield SidePanel()
        yield CompletionMenu()
        yield Composer()

    async def on_mount(self) -> None:
        self.runtime = self.runtime_factory()
        self.session_store = SessionStore()
        self.session_created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.settings.model_alias = self.runtime.model_alias
        self.add_class("drawer-closed")
        self._apply_breakpoint(self.size.width)
        await self._mount_welcome()
        self.query_one(PromptInput).focus()
        self.completion_files = await asyncio.to_thread(workspace_files)
        self._refresh_status()

    def on_unmount(self) -> None:
        if self.worker:
            self.worker.cancel()
        if self.poller and not self.poller.done():
            self.poller.cancel()
        self._cancel_bilibili_login()
        self._save_session()
        if self.runtime:
            self.runtime.close()

    def on_resize(self, event: Resize) -> None:
        self._apply_breakpoint(event.size.width)

    async def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "prompt-input":
            return
        text = event.text_area.text
        suggestions = command_suggestions(text)
        query = file_query(text)
        if query is not None:
            suggestions = file_suggestions(query, self.completion_files)
        self.query_one(CompletionMenu).set_items(suggestions)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "completion-menu":
            self._apply_completion()

    def on_prompt_input_completion_requested(self, _event: PromptInput.CompletionRequested) -> None:
        self._apply_completion()

    async def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        self.query_one(CompletionMenu).set_items([])
        if event.text.startswith("/"):
            await self._handle_command(event.text)
            return
        kind = "shell" if event.text.startswith("!") else "agent"
        text = event.text[1:].strip() if kind == "shell" else event.text
        if not text:
            return
        request = QueuedRequest(kind, text, tuple(self.pending_images), replace(self.settings))
        self.pending_images.clear()
        if self.busy:
            if len(self.request_queue) >= self.request_queue.maxlen:
                await self._notice("Task queue is full (10)", "warning")
            else:
                self.request_queue.append(request)
                await self._notice(f"Queued: {text[:80]}")
                self._refresh_status()
            return
        await self._start_request(request)

    async def _start_request(self, request: QueuedRequest) -> None:
        assert self.runtime is not None
        self.current_request = request
        shown = f"!{request.text}" if request.kind == "shell" else request.text
        await self.query_one(ChatContainer).add_user_message(shown)
        self.current_message = await self.query_one(ChatContainer).add_assistant_message()
        self.pending_turn_message = False
        self.tool_cards.clear()
        self.prompt_tokens = 0
        options = RuntimeOptions(
            planning_mode=request.settings.planning_mode,
            video_type=request.settings.video_type,
            image_paths=request.image_paths,
            permission_mode=request.settings.permission_mode,
        )
        direct = ("bash", {"command": request.text}) if request.kind == "shell" else None
        self.worker = AgentWorker(self.runtime, request.text, options, direct_tool=direct)
        self.busy = True
        self._set_activity("thinking")
        self.worker_thread = threading.Thread(target=self.worker.run, daemon=True)
        self.worker_thread.start()
        self.poller = asyncio.create_task(self._poll_events(self.worker))
        self.poller.add_done_callback(self._consume_poller_result)
        self._refresh_status()
        # Claude-style input remains available while a task is running so the
        # next request can be queued without waiting for the current response.
        self.query_one(PromptInput).focus()

    async def _poll_events(self, worker: AgentWorker) -> None:
        ui_failed = False
        terminal_seen = False
        try:
            while True:
                event = await worker.queue.get()
                if isinstance(event, AgentEvent) and event.kind == "worker_finished":
                    if not terminal_seen and not ui_failed:
                        await self._notice("Worker stopped without a terminal result", "error")
                    break
                if isinstance(event, PermissionRequest):
                    if ui_failed:
                        worker.resolve_permission(event.request_id, False)
                        continue
                    self._set_activity("waiting for permission", event.tool_name)
                    self.app.push_screen(
                        PermissionModal(event.tool_name, event.arguments),
                        lambda approved, request_id=event.request_id: worker.resolve_permission(request_id, bool(approved)),
                    )
                    continue
                if ui_failed:
                    continue
                try:
                    await self._dispatch_agent_event(event)
                    terminal_seen = terminal_seen or event.kind in {"run_finished", "error"}
                except Exception as exc:  # noqa: BLE001 - isolate presentation failures
                    ui_failed = True
                    worker.cancel()
                    queued = len(self.request_queue)
                    self.request_queue.clear()
                    self._set_activity("stopping after UI error")
                    await self._report_ui_failure(exc, event.kind, queued)
                    terminal_seen = terminal_seen or event.kind in {"run_finished", "error"}
        except asyncio.CancelledError:
            worker.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 - never leak poller failures
            ui_failed = True
            worker.cancel()
            queued = len(self.request_queue)
            self.request_queue.clear()
            if self.is_mounted:
                self._set_activity("stopping after UI error")
                await self._report_ui_failure(exc, "poller", queued)
        finally:
            if self.is_mounted:
                await self._finish_run(continue_queue=not ui_failed)

    async def _dispatch_agent_event(self, event: AgentEvent) -> None:
        data = event.data
        if event.kind in {"runtime_ready", "model_changed"}:
            panel = self.query(WelcomePanel).first()
            if panel:
                panel.set_model(str(data.get("model") or "--"))
        elif event.kind == "turn_started":
            turn = int(data.get("turn") or 0)
            if turn > 1:
                if self.current_message:
                    self.current_message.clear_activity()
                    if await self.current_message.discard_if_empty():
                        self.current_message = None
                self.pending_turn_message = True
            stage = self.activity_tool if turn > 1 else ""
            self._set_activity("thinking", stage, turn=turn)
        elif event.kind == "text_delta":
            text = str(data.get("text") or "")
            if text:
                await self._ensure_turn_message()
                assert self.current_message is not None
                await self.current_message.append_token(text)
                self._follow_output()
        elif event.kind == "status":
            status = str(data.get("status") or "idle")
            tool = str(data.get("tool") or "")
            if status == "thinking" and not tool and self.activity_tool.startswith("after "):
                tool = self.activity_tool
            self._set_activity(status, tool)
        elif event.kind == "tool_started":
            await self._ensure_turn_message()
            call_id = str(data.get("call_id") or "")
            card = ToolCallCard(call_id, str(data.get("name") or "tool"), data.get("arguments") or {})
            self.tool_cards[call_id] = card
            if self.current_message:
                self.current_message.mark_tool_output()
                await self.current_message.mount_tool_card(card)
                self._follow_output()
            self._set_activity("running tool", str(data.get("name") or "tool"))
        elif event.kind == "tool_finished":
            card = self.tool_cards.get(str(data.get("call_id") or ""))
            status = str(data.get("status") or "error")
            result = redact_text(data.get("result"), max_chars=3000)
            tool_name = str(data.get("name") or (card.tool_name if card else "tool"))
            if card:
                display_status = (
                    "retrying"
                    if status == "error" and tool_name == "kb_write" and "[参数层]" in result
                    else status
                )
                card.finish(
                    display_status,
                    result,
                    int(data.get("duration_ms") or 0),
                )
            if status == "done":
                for previous in self.tool_cards.values():
                    if previous is not card and previous.tool_name == tool_name:
                        previous.recover()
            self._set_activity("thinking", f"after {tool_name}")
            self._follow_output()
        elif event.kind == "todo_changed":
            self.query_one(SidePanel).set_todo(str(data.get("rendered") or ""))
        elif event.kind == "usage":
            self.prompt_tokens += int(data.get("total_tokens") or 0)
        elif event.kind == "artifact":
            path = str(data.get("path") or "")
            if path and path not in [item.path for item in self.artifacts]:
                self.artifacts.append(ArtifactRecord(str(data.get("kind_name") or "file"), path))
                self.query_one(SidePanel).set_artifacts([item.path for item in self.artifacts])
        elif event.kind == "notice":
            await self._notice(str(data.get("message") or ""), str(data.get("level") or "info"))
        elif event.kind == "error":
            if self.current_message:
                if await self.current_message.discard_if_empty():
                    self.current_message = None
            await self._notice("Error: " + redact_text(data.get("message"), max_chars=1000), "error")
            self._set_activity("idle")
        elif event.kind == "run_finished":
            for card in self.tool_cards.values():
                if card.status == "retrying":
                    card.finish("error", card.result, card.duration_ms)
            content = str(data.get("content") or "")
            if content.strip() and (
                self.pending_turn_message
                or self.current_message is None
                or not self.current_message.content.strip()
            ):
                await self._ensure_turn_message()
                assert self.current_message is not None
                await self.current_message.append_token(content)
                self._follow_output()
            if self.current_message:
                await self.current_message.finalize()
                if await self.current_message.discard_if_empty():
                    self.current_message = None
            self.pending_turn_message = False
            self._set_activity("completed")
            self._follow_output()
            if self.current_request and self.current_request.kind == "shell" and self.runtime:
                self.runtime.history.extend([
                    {"role": "user", "content": f"!{self.current_request.text}"},
                    {"role": "assistant", "content": str(data.get("content") or "")},
                ])
        self._refresh_status()

    async def _finish_run(self, *, continue_queue: bool = True) -> None:
        self.worker = None
        self.worker_thread = None
        self._save_session()
        if continue_queue and self.request_queue:
            request = self.request_queue.popleft()
            await self._start_request(request)
        else:
            self.busy = False
            self._set_activity("idle")
            self._refresh_status()
            self.query_one(PromptInput).focus()

    async def _report_ui_failure(self, exc: Exception, event_kind: str, queued: int) -> None:
        message = redact_text(f"{type(exc).__name__}: {exc}", max_chars=500)
        if self.runtime and self.runtime.tracer:
            self.runtime.tracer.record(
                "ui",
                "render_error",
                ok=False,
                input_data={"event_kind": event_kind, "queue_cleared": queued},
                output=message,
            )
        await self._notice(
            f"UI rendering failed during {event_kind}. The current task is stopping"
            f" and {queued} queued task(s) were cleared. {message}",
            "error",
        )

    async def _ensure_turn_message(self) -> None:
        if self.current_message is None or self.pending_turn_message:
            if self.current_message:
                self.current_message.clear_activity()
            self.current_message = await self.query_one(ChatContainer).add_assistant_message()
            self.pending_turn_message = False
            self.current_message.set_activity(
                self.activity_status,
                self.activity_tool,
                self.activity_turn,
            )

    def _follow_output(self) -> None:
        self.query_one(ChatContainer).follow_output()

    def _consume_poller_result(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            self.log.error(f"Unhandled TUI poller error: {type(error).__name__}: {error}")

    async def _handle_command(self, raw: str) -> None:
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            await self._notice(f"Invalid command: {exc}", "error")
            return
        command, args = parts[0].lower(), parts[1:]
        if command == "/help":
            await self._notice(HELP_TEXT)
        elif command in {"/new", "/clear"}:
            if self.busy:
                await self._notice("Interrupt or wait for the current task first", "warning")
            elif command == "/new":
                await self._new_session()
            else:
                assert self.runtime is not None
                self.runtime.clear()
                await self._reset_conversation("Context cleared")
                self._save_session()
        elif command == "/sessions":
            records = self.session_store.list() if self.session_store else []
            await self._notice("\n".join(f"{item.session_id}  {item.updated_at}  {item.title}" for item in records) or "No saved sessions")
        elif command == "/resume":
            if self.busy:
                await self._notice("Interrupt or wait for the current task first", "warning")
            elif args:
                await self._resume_session(args[0])
            else:
                records = self.session_store.list() if self.session_store else []
                choices = [(item.session_id, f"{item.updated_at}  {item.title}") for item in records]
                if not choices:
                    await self._notice("No sessions available in this workspace")
                else:
                    self.app.push_screen(
                        ChoiceModal("Resume session", choices),
                        lambda value: asyncio.create_task(self._resume_session(value)) if value else None,
                    )
        elif command == "/compact":
            if self.busy:
                await self._notice("Cannot compact while a task is running", "warning")
            else:
                assert self.runtime is not None
                self._set_activity("compacting")
                result = await asyncio.to_thread(self.runtime.compact_history)
                self._set_activity("idle")
                await self._notice(f"Context compacted: {result['before']} -> {result['after']} tokens", "success")
                self._save_session()
        elif command == "/model":
            await self._choose_model(args[0] if args else "")
        elif command == "/permissions":
            await self._set_permission(args[0] if args else "")
        elif command == "/image":
            await self._add_image(args)
        elif command == "/images":
            await self._notice("\n".join(self.pending_images) if self.pending_images else "No pending images")
        elif command == "/plan":
            value = args[0].lower() if args else ""
            mapping = {"auto": "auto", "on": "force", "off": "off"}
            if value not in mapping:
                await self._notice("Usage: /plan auto|on|off", "error")
            else:
                self.settings.planning_mode = mapping[value]
                self._save_session()
        elif command == "/video-type":
            value = args[0].lower() if args else ""
            allowed = {"auto", "tutorial", "knowledge", "narrative", "commentary", "general"}
            if value not in allowed:
                await self._notice("Usage: /video-type auto|tutorial|knowledge|narrative|commentary|general", "error")
            else:
                self.settings.video_type = value
                self._save_session()
        elif command == "/bilibili-login":
            await self._bilibili_login()
        elif command == "/bilibili-status":
            await self._bilibili_status()
        elif command == "/bilibili-logout":
            await self._bilibili_logout()
        elif command in {"/trace", "/cost"}:
            assert self.runtime is not None
            if not self.runtime.tracer:
                await self._notice("Trace is disabled for this session", "warning")
            else:
                summary = self.runtime.tracer.summary()
                text = json.dumps(summary, ensure_ascii=False) if command == "/cost" else f"{self.runtime.tracer.path}\n{json.dumps(summary, ensure_ascii=False)}"
                await self._notice(text)
        elif command == "/copy":
            await self._copy_response(args)
        elif command == "/open":
            await self._open_artifact(args)
        elif command == "/quit":
            self.app.exit()
        else:
            await self._notice(f"Unknown command: {command}. Enter /help to list commands", "error")
        self._refresh_status()

    async def _choose_model(self, alias: str) -> None:
        assert self.runtime is not None
        if alias:
            try:
                profile = self.runtime.switch_model(alias)
            except ValueError as exc:
                await self._notice(str(exc), "error")
                return
            self.settings.model_alias = profile.alias
            panel = self.query(WelcomePanel).first()
            if panel:
                panel.set_model(self.runtime.model_name)
            self._save_session()
            return
        choices = [(profile.alias, f"{profile.alias:<12} {profile.default_model}") for profile in self.runtime.available_models]
        self.app.push_screen(
            ChoiceModal("Select model", choices),
            lambda value: asyncio.create_task(self._choose_model(value)) if value else None,
        )

    async def _copy_response(self, args: list[str]) -> None:
        if args:
            await self._notice("Usage: /copy", "error")
            return
        messages = [message for message in self.query(AssistantMessage) if message.content.strip()]
        if not messages:
            await self._notice("No completed response to copy", "warning")
            return
        messages[-1].copy_content()

    async def _set_permission(self, mode: str) -> None:
        if mode:
            normalized = next((item for item in PERMISSION_MODES if item.lower() == mode.lower()), "")
            if not normalized:
                await self._notice("Usage: /permissions default|acceptEdits|plan", "error")
                return
            self.settings.permission_mode = normalized
            panel = self.query(WelcomePanel).first()
            if panel:
                panel.set_permission(normalized)
            self._save_session()
            return
        choices = [(item, item) for item in PERMISSION_MODES]
        self.app.push_screen(
            ChoiceModal("Permission mode", choices),
            lambda value: asyncio.create_task(self._set_permission(value)) if value else None,
        )

    async def _add_image(self, args: list[str]) -> None:
        if not args:
            await self._notice("Usage: /image <path>", "error")
            return
        path = str(Path(" ".join(args)).expanduser())
        try:
            image_block(path)
        except (OSError, ValueError) as exc:
            await self._notice(f"Image unavailable: {exc}", "error")
            return
        self.pending_images.append(path)

    async def _bilibili_login(self) -> None:
        if self.bilibili_login_task and not self.bilibili_login_task.done():
            await self._notice("Bilibili login is already waiting for confirmation", "warning")
            return
        assert self.runtime is not None
        from tools.bilibili_auth import begin_qr_login, render_qr_ascii

        try:
            challenge = await asyncio.to_thread(
                begin_qr_login,
                session=self.runtime.bilibili_auth_session,
            )
            qr_text = await asyncio.to_thread(render_qr_ascii, challenge.url)
        except Exception as exc:
            await self._notice(f"Bilibili login unavailable: {type(exc).__name__}: {exc}", "error")
            return
        modal = BilibiliLoginModal(qr_text)
        self.app.push_screen(modal)
        self.bilibili_login_task = asyncio.create_task(self._poll_bilibili_login(challenge, modal))

    def _cancel_bilibili_login(self) -> None:
        if self.bilibili_login_task and not self.bilibili_login_task.done():
            self.bilibili_login_task.cancel()
        self.bilibili_login_task = None

    async def _poll_bilibili_login(self, challenge, modal: BilibiliLoginModal) -> None:
        from tools.bilibili_auth import poll_qr_once

        labels = {
            "waiting_scan": "Scan with the Bilibili mobile app",
            "scanned_waiting_confirmation": "Scanned. Confirm login on your phone.",
            "success": "Login active for this terminal only, for at most 30 minutes.",
            "expired": "QR code expired. Close and run /bilibili-login again.",
            "error": "Login request failed. Close and retry.",
        }
        try:
            for _ in range(100):
                if modal.is_mounted:
                    break
                await asyncio.sleep(0.05)
            if not modal.is_mounted:
                return
            for _ in range(90):
                if not modal.is_mounted:
                    return
                assert self.runtime is not None
                result = await asyncio.to_thread(
                    poll_qr_once,
                    challenge,
                    session=self.runtime.bilibili_auth_session,
                )
                status = str(result.get("status") or "error")
                modal.update_status(labels.get(status, status))
                if status in {"success", "expired", "error"}:
                    return
                await asyncio.sleep(2)
            modal.update_status("Login timed out. Close and retry.")
        finally:
            challenge.client.close()

    async def _bilibili_status(self) -> None:
        assert self.runtime is not None
        from tools.bilibili_auth import auth_status

        result = await asyncio.to_thread(
            auth_status,
            session=self.runtime.bilibili_auth_session,
        )
        remaining = result.get("expires_in_seconds")
        suffix = f", {remaining}s remaining" if remaining is not None and result["status"] == "valid" else ""
        await self._notice(
            f"Bilibili subtitle login: {result['status']} ({result['mode']}{suffix})"
        )

    async def _bilibili_logout(self) -> None:
        assert self.runtime is not None
        from tools.bilibili_auth import logout

        await asyncio.to_thread(logout, session=self.runtime.bilibili_auth_session)
        await self._notice("Bilibili subtitle login removed", "success")

    async def _open_artifact(self, args: list[str]) -> None:
        if not args or not args[0].isdigit():
            await self._notice("Usage: /open <n>", "error")
            return
        index = int(args[0]) - 1
        if index < 0 or index >= len(self.artifacts):
            await self._notice("Artifact number does not exist", "error")
            return
        artifact_path = self.artifacts[index].path
        try:
            candidate = resolve_artifact(artifact_path)
        except (OSError, ValueError) as exc:
            await self._notice(f"Could not open: {exc}", "error")
            return
        if ArtifactPreviewModal.supports(candidate):
            self.app.push_screen(ArtifactPreviewModal(candidate))
            return
        ok, message = open_artifact(artifact_path)
        await self._notice(("Opened: " if ok else "Could not open: ") + message, "success" if ok else "error")

    async def _new_session(self) -> None:
        self._save_session()
        self._cancel_bilibili_login()
        if self.runtime:
            self.runtime.close()
        self.runtime = self.runtime_factory()
        self.settings = TUISettings(model_alias=self.runtime.model_alias)
        self.artifacts.clear()
        self.pending_images.clear()
        self.request_queue.clear()
        self.session_created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await self._reset_conversation("New session")

    async def _resume_session(self, session_id: str | None) -> None:
        if not session_id or not self.session_store:
            return
        try:
            record = self.session_store.load(session_id)
        except ValueError as exc:
            await self._notice(str(exc), "error")
            return
        self._cancel_bilibili_login()
        if self.runtime:
            self.runtime.close()
        self.runtime = self.runtime_factory()
        self.runtime.session_id = record.session_id
        self.runtime.history = record.history
        self.session_created_at = record.created_at
        self.settings = TUISettings(
            planning_mode=record.settings.get("planning_mode", "auto"),
            video_type=record.settings.get("video_type", "auto"),
            permission_mode=record.settings.get("permission_mode", "default"),
            model_alias=record.settings.get("model_alias", self.runtime.model_alias),
        )
        try:
            self.runtime.switch_model(self.settings.model_alias)
        except ValueError:
            self.settings.model_alias = self.runtime.model_alias
        self.artifacts = [ArtifactRecord(item.get("kind", "file"), item.get("path", "")) for item in record.artifacts]
        await self.query_one(ChatContainer).clear_messages()
        await self._mount_welcome()
        for message in record.history:
            content = _history_text(message.get("content"))
            if not content:
                continue
            if message.get("role") == "user":
                await self.query_one(ChatContainer).add_user_message(content)
            elif message.get("role") == "assistant":
                assistant = await self.query_one(ChatContainer).add_assistant_message()
                await assistant.append_token(content)
                await assistant.finalize()
        self.query_one(SidePanel).set_artifacts([item.path for item in self.artifacts])
        await self._notice(f"Resumed: {record.title}", "success")
        self._refresh_status()

    async def _reset_conversation(self, notice: str) -> None:
        await self.query_one(ChatContainer).clear_messages()
        assert self.runtime is not None
        await self._mount_welcome()
        await self._notice(notice, "success")
        self.query_one(SidePanel).set_todo("")
        self.query_one(SidePanel).set_artifacts([])
        self._refresh_status()

    def _save_session(self) -> None:
        if not self.runtime or not self.session_store or not self.runtime.history:
            return
        self.session_store.save(
            self.runtime.session_id,
            self.runtime.history,
            settings={
                "planning_mode": self.settings.planning_mode,
                "video_type": self.settings.video_type,
                "permission_mode": self.settings.permission_mode,
                "model_alias": self.settings.model_alias,
            },
            artifacts=[{"kind": item.kind, "path": item.path} for item in self.artifacts],
            created_at=self.session_created_at,
        )

    def _apply_completion(self) -> None:
        menu = self.query_one(CompletionMenu)
        value = menu.selected_value()
        if not value:
            return
        prompt = self.query_one(PromptInput)
        prompt.text = replace_completion(prompt.text, value)
        prompt.move_cursor((prompt.document.line_count - 1, len(prompt.document.lines[-1])))
        menu.set_items([])
        prompt.focus()

    async def _notice(self, text: str, variant: str = "info") -> None:
        await self.query_one(ChatContainer).add_system_message(text, variant)

    def _refresh_status(self) -> None:
        if not self.runtime:
            return
        usage = self.runtime.context_usage()
        self.query_one(SidePanel).set_settings(
            self.settings.planning_mode,
            self.settings.video_type,
            self.settings.permission_mode,
            len(self.pending_images),
        )
        trace_path = str(self.runtime.tracer.path) if self.runtime.tracer else ""
        trace_tokens = self.runtime.tracer.summary()["total_tokens"] if self.runtime.tracer else 0
        self.query_one(SidePanel).set_trace(trace_path, trace_tokens)
        self.query_one(Composer).set_state(
            permission=self.settings.permission_mode,
            model=self.settings.model_alias,
            context_percent=float(usage["percent"]),
            images=self.pending_images,
            queued=len(self.request_queue),
            busy=self.busy,
        )

    def action_cancel(self) -> None:
        if self.worker and self.busy:
            self.worker.cancel()
            self._set_activity("interrupting")
        else:
            self.query_one(CompletionMenu).set_items([])

    def action_quit(self) -> None:
        if not self.busy:
            self.app.exit()

    def action_cycle_permission(self) -> None:
        index = PERMISSION_MODES.index(self.settings.permission_mode)
        self.settings.permission_mode = PERMISSION_MODES[(index + 1) % len(PERMISSION_MODES)]
        panel = self.query(WelcomePanel).first()
        if panel:
            panel.set_permission(self.settings.permission_mode)
        self._save_session()
        self._refresh_status()

    def action_toggle_drawer(self) -> None:
        self.drawer_open = not self.drawer_open
        self.set_class(self.drawer_open, "drawer-open")
        self.set_class(not self.drawer_open, "drawer-closed")

    async def _mount_welcome(self) -> None:
        assert self.runtime is not None
        records = self.session_store.list(limit=3) if self.session_store else []
        recent = [(item.session_id, item.title) for item in records]
        await self.query_one(ChatContainer).add_welcome(
            self.runtime.model_name,
            str(Path.cwd()),
            self.settings.permission_mode,
            recent,
        )

    def _set_activity(self, status: str, tool: str = "", turn: int = 0) -> None:
        self.activity_status = status
        self.activity_tool = tool
        if turn:
            self.activity_turn = turn
        if self.current_message and self.current_message.is_mounted:
            self.current_message.set_activity(status, tool, self.activity_turn)
        self._refresh_status()

    def _apply_breakpoint(self, width: int) -> None:
        self.set_class(width < 100, "narrow")
        self.set_class(width < 70, "compact")


def _history_text(content: object) -> str:
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    return str(content or "")
