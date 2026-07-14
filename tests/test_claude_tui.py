from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from agent.events import AgentEvent
from agent.runtime import AgentRuntime, RuntimeOptions, load_model_profiles
from agent.session import MAX_SESSION_BYTES, SessionStore
from backend.client import DeepSeekBackend
from tui.app import MiniOpenClawApp
from tui.chat_view import ActivityLine, AssistantMessage, WelcomePanel
from tui.composer import Composer
from tui.completion import CompletionMenu, workspace_files
from tui.input_area import PromptInput
from tui.modals import BilibiliLoginModal
from tui.screens import MainScreen


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class SlowBackend:
    model = "slow-test"

    def __init__(self):
        self.calls = 0
        self.first_call_started = threading.Event()
        self.release_first_call = threading.Event()

    def chat_stream(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            self.first_call_started.set()
            self.release_first_call.wait(timeout=10)
        yield {"type": "content", "delta": f"reply-{self.calls}"}
        yield {"type": "usage", "prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
        yield {"type": "done"}


class SessionStoreTests(unittest.TestCase):
    def test_roundtrip_redacts_secrets_and_omits_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root / "sessions", workdir=root)
            history = [
                {"role": "user", "content": [
                    {"type": "text", "text": "api_key=sk-abcdefghijklmnopqrstuvwxyz"},
                    {"type": "image", "source": {"data": "very-secret-image-data"}},
                ]},
                {"role": "assistant", "content": "done"},
            ]
            path = store.save("session-one", history, settings={"model_alias": "deepseek"})
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", text)
            self.assertNotIn("very-secret-image-data", text)
            self.assertIn("image omitted", text)
            record = store.load("session-one")
            self.assertEqual(record.settings["model_alias"], "deepseek")

    def test_session_file_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = [
                {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 20_000}
                for index in range(80)
            ]
            path = SessionStore(root / "sessions", workdir=root).save("bounded", history)
            self.assertLessEqual(path.stat().st_size, MAX_SESSION_BYTES)

    def test_lists_only_current_workspace_and_skips_corrupt_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            SessionStore(sessions, workdir=root).save("local", [{"role": "user", "content": "hello"}])
            sessions.joinpath("broken.json").write_text("{", encoding="utf-8")
            other = root / "other"
            other.mkdir()
            SessionStore(sessions, workdir=other).save("other", [{"role": "user", "content": "other"}])
            self.assertEqual([item.session_id for item in SessionStore(sessions, workdir=root).list()], ["local"])


class RuntimeExtensionTests(unittest.TestCase):
    def test_runtime_closes_injected_backend(self):
        class ClosableBackend:
            model = "close-test"

            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        backend = ClosableBackend()
        runtime = AgentRuntime(backend=backend, trace_enabled=False, enable_mcp=False)
        runtime.close()
        self.assertTrue(backend.closed)

    def test_workspace_files_handles_missing_stdout(self):
        result = SimpleNamespace(stdout=None)
        with tempfile.TemporaryDirectory() as tmp, patch(
            "tui.completion.subprocess.run", return_value=result
        ):
            self.assertEqual(workspace_files(Path(tmp)), [])

    def test_mcp_starts_only_echo_and_calc_without_notices(self):
        events = []
        runtime = AgentRuntime(trace_enabled=False, enable_mcp=True, event_sink=events.append)
        try:
            with patch("tools.mcp_client.MCPClient") as client_class, patch(
                "tools.mcp_client.register_mcp_tools"
            ):
                runtime._ensure_mcp()
            candidates = [
                (call.args[0], call.kwargs["name"])
                for call in client_class.call_args_list
            ]
            notices = [event.data for event in events if event.kind == "notice"]
            self.assertEqual([name for _command, name in candidates], ["echo", "calc"])
            self.assertFalse(any(command[0] == "npx" for command, _name in candidates))
            self.assertEqual(notices, [])
        finally:
            runtime.close()

    def test_backend_accepts_root_or_v1_base_url(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            root = DeepSeekBackend(base_url="https://example.com")
            versioned = DeepSeekBackend(base_url="https://example.com/v1")
            self.assertEqual(root.chat_completions_url, "https://example.com/v1/chat/completions")
            self.assertEqual(versioned.chat_completions_url, "https://example.com/v1/chat/completions")
            root._client.close()
            versioned._client.close()

    def test_compact_context_and_plan_mode(self):
        runtime = AgentRuntime(trace_enabled=False, enable_mcp=False)
        runtime.history = [
            {"role": "user", "content": f"question {index}"} if index % 2 == 0
            else {"role": "assistant", "content": "answer " * 100}
            for index in range(12)
        ]
        before = runtime.context_usage()["used"]
        result = runtime.compact_history()
        self.assertEqual(result["before"], before)
        self.assertLessEqual(result["after"], result["before"])
        self.assertTrue(any(item.get("_history_memo") for item in runtime.history))
        denied = runtime.execute_direct_tool("bash", {"command": "echo hello"}, permission_mode="plan")
        self.assertIn("Plan 模式禁止", denied)
        runtime.close()

    def test_accept_edits_does_not_auto_approve_shell(self):
        runtime = AgentRuntime(trace_enabled=False, enable_mcp=False, confirm_callback=lambda *_: False)
        result = runtime.execute_direct_tool("bash", {"command": "echo hello"}, permission_mode="acceptEdits")
        self.assertIn("用户拒绝", result)
        runtime.close()

    def test_plan_schema_hides_mutating_tools(self):
        from agent.policy import ToolPolicy
        from tools.base import build_default_registry

        names = {
            item["function"]["name"]
            for item in ToolPolicy(permission_mode="plan").schemas(build_default_registry())
        }
        self.assertNotIn("write", names)
        self.assertNotIn("edit", names)
        self.assertNotIn("bash", names)
        self.assertEqual(ToolPolicy(permission_mode="plan").authorize("write", {"path": "a.txt"})[0], "deny")

    def test_model_aliases_are_environment_backed(self):
        custom = {
            "custom": {
                "api_key_env": "CUSTOM_KEY",
                "base_url_env": "CUSTOM_URL",
                "model_env": "CUSTOM_MODEL",
                "default_base_url": "https://example.com",
                "default_model": "custom-chat",
                "context_window": 32000,
                "supports_images": False,
            }
        }
        with patch.dict(os.environ, {
            "MINI_OPENCLAW_MODEL_ALIASES": json.dumps(custom),
            "CUSTOM_KEY": "test-key",
        }, clear=False):
            profiles = load_model_profiles()
            self.assertTrue(profiles["custom"].configured)
            runtime = AgentRuntime(trace_enabled=False, enable_mcp=False)
            profile = runtime.switch_model("custom")
            self.assertEqual(profile.context_window, 32000)
            self.assertEqual(runtime.model_alias, "custom")
            runtime.close()


class BrandAssetTests(unittest.TestCase):
    def test_welcome_logo_identifies_video_knowledge_terminal(self):
        from tui.chat_view import KNOWLEDGE_TERMINAL_ASCII

        self.assertIn("VIDEO + KB", KNOWLEDGE_TERMINAL_ASCII)
        self.assertEqual(len(KNOWLEDGE_TERMINAL_ASCII.splitlines()), 5)

    def test_pixel_terminal_assets_are_rgba_and_old_mascot_is_removed(self):
        root = Path(__file__).parents[1] / "tui"
        self.assertFalse((root / "scared.png").exists())
        for size in (128, 512):
            path = root / "assets" / f"knowledge-terminal-{size}.png"
            with Image.open(path) as image:
                self.assertEqual(image.size, (size, size))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                bounds = image.getbbox()
                assert bounds is not None
                self.assertGreater(bounds[2] - bounds[0], size // 2)


class PackagingTests(unittest.TestCase):
    def test_requirements_install_project_and_register_tui_command(self):
        root = Path(__file__).parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        active = [line.strip() for line in requirements if line.strip() and not line.startswith("#")]

        self.assertEqual(project["project"]["scripts"]["mini-openclaw"], "tui.__main__:main")
        self.assertEqual(active, ["-e ."])
        self.assertNotIn("dynamic", project["project"])
        self.assertIn("FASTER_WHISPER_MODEL_PATH=/app/models/faster-whisper-base", dockerfile)
        self.assertIn("BILIBILI_AUTH_MODE=ephemeral", dockerfile)
        self.assertNotIn("snapshot_download", dockerfile)
        self.assertIn(".mini-openclaw/", dockerignore)
        self.assertIn("knowledge_base/*", dockerignore)
        self.assertIn("*.zip", dockerignore)
        self.assertNotIn("models/", dockerignore)

    def test_generic_runtime_loads_project_mcp_client_outside_source_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from agent.runtime import AgentRuntime; "
                    "runtime = AgentRuntime(trace_enabled=False, enable_mcp=True); "
                    "runtime._ensure_mcp(); "
                    "assert any(name.startswith('mcp__') for name in runtime.base_registry.names()); "
                    "runtime.close()",
                ],
                cwd=tmp,
                env={**os.environ, "PATH": ""},
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)


class ClaudeStyleTUITests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def runtime_factory() -> AgentRuntime:
        return AgentRuntime(trace_enabled=False, enable_mcp=False)

    async def test_bilibili_login_command_opens_nonblocking_qr_modal(self):
        class Client:
            def close(self):
                return None

        challenge = SimpleNamespace(url="https://example.test/qr", client=Client())
        with patch("tools.bilibili_auth.begin_qr_login", return_value=challenge), patch(
            "tools.bilibili_auth.render_qr_ascii", return_value="QR-CONTENT"
        ), patch("tools.bilibili_auth.poll_qr_once", return_value={"status": "expired"}):
            app = MiniOpenClawApp(self.runtime_factory)
            async with app.run_test(size=(120, 40)) as pilot:
                prompt = app.screen.query_one(PromptInput)
                prompt.text = "/bilibili-login"
                await pilot.press("enter")
                await pilot.pause(0.5)
                self.assertIsInstance(app.screen, BilibiliLoginModal)
                self.assertIn("expired", str(app.screen.query_one("#bilibili-login-status").render()).lower())
                await pilot.press("escape")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, MainScreen)

    async def test_slash_file_completion_permission_cycle_and_drawer(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            self.assertIsInstance(screen, MainScreen)
            prompt = screen.query_one("#prompt-input")
            prompt.text = "/com"
            await pilot.pause()
            self.assertTrue(screen.query_one(CompletionMenu).display)
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(prompt.text, "/compact ")

            prompt.text = "inspect @tui/sc"
            await pilot.pause()
            self.assertTrue(screen.query_one(CompletionMenu).display)
            await pilot.press("tab")
            self.assertIn("@tui/", prompt.text)

            self.assertEqual(screen.settings.permission_mode, "default")
            await pilot.press("shift+tab")
            self.assertEqual(screen.settings.permission_mode, "acceptEdits")
            self.assertTrue(screen.query_one(Composer).has_class("permission-accept-edits"))
            await pilot.press("ctrl+b")
            self.assertTrue(screen.drawer_open)

    async def test_welcome_panel_uses_recent_workspace_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = MiniOpenClawApp(self.runtime_factory)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                screen = app.screen
                store = SessionStore(Path(tmp) / "sessions", workdir=Path.cwd())
                store.save("recent-one", [{"role": "user", "content": "Summarize a Bilibili tutorial"}])
                screen.session_store = store
                await screen._reset_conversation("Ready")
                await pilot.pause()
                panel = screen.query_one(WelcomePanel)
                self.assertIn("mini-openclaw v", str(panel.border_title))
                self.assertIn("recent-one", str(panel.query_one(".recent-sessions").render()))
                self.assertGreater(panel.query_one(".welcome-right").region.width, 0)

    async def test_compact_welcome_and_composer_do_not_overlap(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(60, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            self.assertTrue(screen.has_class("compact"))
            panel = screen.query_one(WelcomePanel)
            self.assertFalse(panel.query_one(".welcome-right").display)
            workspace = screen.query_one("#workspace")
            composer = screen.query_one(Composer)
            self.assertLessEqual(workspace.region.bottom, composer.region.y)
            self.assertLessEqual(panel.region.right, screen.query_one("ChatContainer").region.right)

    async def test_activity_line_tracks_running_state(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            message = await screen.query_one("ChatContainer").add_assistant_message()
            screen.current_message = message
            screen._set_activity("running tool", "video_probe", 2)
            await pilot.pause()
            activity = message.query_one(ActivityLine)
            self.assertTrue(activity.display)
            self.assertIn("video_probe", str(activity.render()))
            screen._set_activity("completed")
            self.assertFalse(activity.display)

    async def test_completed_response_copy_button_preserves_markdown(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(60, 30)) as pilot:
            await pilot.pause()
            chat = app.screen.query_one("ChatContainer")
            message = await chat.add_assistant_message()
            markdown = "# 中文标题\n\n```python\nprint('hello')\n```\n"
            await message.append_token(markdown)
            actions = message.query_one(".assistant-actions")
            self.assertFalse(actions.display)

            await message.finalize()
            await pilot.pause()
            self.assertTrue(actions.display)
            button = message.query_one(".copy-response")
            self.assertLessEqual(button.region.right, message.region.right)
            button.press()
            await pilot.pause()
            self.assertEqual(app.clipboard, markdown)

    async def test_copy_command_uses_latest_completed_response(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            chat = screen.query_one("ChatContainer")
            first = await chat.add_assistant_message()
            await first.append_token("first")
            await first.finalize()
            second = await chat.add_assistant_message()
            await second.append_token("**second**")
            await second.finalize()

            await screen._handle_command("/copy")
            self.assertEqual(app.clipboard, "**second**")
            await screen._handle_command("/copy extra")
            self.assertEqual(app.clipboard, "**second**")

    async def test_empty_response_cannot_be_copied(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            message = await screen.query_one("ChatContainer").add_assistant_message()
            await message.finalize()
            self.assertFalse(message.query_one(".assistant-actions").display)
            self.assertFalse(message.copy_content())
            await screen._handle_command("/copy")
            self.assertEqual(app.clipboard, "")

    async def test_tool_card_stays_above_activity_line(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            message = await screen.query_one("ChatContainer").add_assistant_message()
            screen.current_message = message
            await screen._dispatch_agent_event(AgentEvent(
                "tool_started",
                {"call_id": "call-order", "name": "read", "arguments": {"path": "README.md"}},
            ))
            await pilot.pause()
            children = list(message.children)
            actions = message.query_one(".assistant-actions")
            activity = message.query_one(ActivityLine)
            self.assertLess(
                children.index(screen.tool_cards["call-order"]),
                children.index(actions),
            )
            self.assertLess(children.index(actions), children.index(activity))
            self.assertIs(children[-1], activity)

    async def test_kb_parameter_retry_card_recovers_after_success(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            await screen._dispatch_agent_event(AgentEvent(
                "tool_started",
                {"call_id": "kb-bad", "name": "kb_write", "arguments": {}},
            ))
            await screen._dispatch_agent_event(AgentEvent(
                "tool_finished",
                {
                    "call_id": "kb-bad",
                    "name": "kb_write",
                    "status": "error",
                    "result": "[参数层] JSON 解析失败",
                    "duration_ms": 10,
                },
            ))
            retry = screen.tool_cards["kb-bad"]
            self.assertEqual(retry.status, "retrying")
            self.assertIn("[retry]", str(retry.title))
            self.assertTrue(retry.collapsed)

            await screen._dispatch_agent_event(AgentEvent(
                "tool_started",
                {"call_id": "kb-ok", "name": "kb_write", "arguments": {"source_url": "BV1"}},
            ))
            await screen._dispatch_agent_event(AgentEvent(
                "tool_finished",
                {
                    "call_id": "kb-ok",
                    "name": "kb_write",
                    "status": "done",
                    "result": '{"ok": true}',
                    "duration_ms": 20,
                },
            ))
            self.assertEqual(retry.status, "recovered")
            self.assertIn("[recovered]", str(retry.title))
            self.assertEqual(screen.tool_cards["kb-ok"].status, "done")

    async def test_unrecovered_parameter_retry_becomes_final_error(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            await screen._dispatch_agent_event(AgentEvent(
                "tool_started",
                {"call_id": "kb-final", "name": "kb_write", "arguments": {}},
            ))
            await screen._dispatch_agent_event(AgentEvent(
                "tool_finished",
                {
                    "call_id": "kb-final",
                    "name": "kb_write",
                    "status": "error",
                    "result": "[参数层] JSON 解析失败",
                    "duration_ms": 10,
                },
            ))
            await screen._dispatch_agent_event(AgentEvent(
                "run_finished", {"status": "error", "content": ""}
            ))
            card = screen.tool_cards["kb-final"]
            self.assertEqual(card.status, "error")
            self.assertIn("[error]", str(card.title))

    async def test_busy_input_queues_and_runs_in_order(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.runtime is not None
            backend = SlowBackend()
            screen.runtime.text_backend = backend
            prompt = screen.query_one("#prompt-input")
            prompt.text = "first"
            await pilot.press("enter")
            for _ in range(40):
                if backend.first_call_started.is_set():
                    break
                await pilot.pause(0.05)
            self.assertTrue(backend.first_call_started.is_set())
            prompt.text = "second"
            await pilot.press("enter")
            await pilot.pause()
            try:
                self.assertEqual(len(screen.request_queue), 1)
            finally:
                backend.release_first_call.set()
            for _ in range(80):
                await pilot.pause(0.05)
                if not screen.busy and not screen.request_queue:
                    break
            self.assertEqual(backend.calls, 2)
            self.assertFalse(screen.busy)

    async def test_busy_queue_is_capped_at_ten(self):
        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.busy = True
            for index in range(12):
                await screen.on_prompt_input_submitted(PromptInput.Submitted(f"queued-{index}"))
            self.assertEqual(len(screen.request_queue), 10)
            self.assertEqual(screen.request_queue[0].text, "queued-0")
            self.assertEqual(screen.request_queue[-1].text, "queued-9")

    async def test_plan_mode_denies_direct_shell_without_modal(self):
        from tui.screens import PermissionModal

        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.settings.permission_mode = "plan"
            prompt = screen.query_one("#prompt-input")
            prompt.text = "!echo hello"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause(0.05)
                if not screen.busy:
                    break
            self.assertNotIsInstance(app.screen, PermissionModal)
            self.assertTrue(any("Plan 模式禁止" in message.content for message in screen.query(AssistantMessage)))

    async def test_session_save_new_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = MiniOpenClawApp(self.runtime_factory)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                screen = app.screen
                screen.session_store = SessionStore(Path(tmp) / "sessions")
                assert screen.runtime is not None
                screen.runtime.history = [
                    {"role": "user", "content": "remember this conversation"},
                    {"role": "assistant", "content": "remembered"},
                ]
                session_id = screen.runtime.session_id
                screen._save_session()
                await screen._new_session()
                self.assertEqual(screen.runtime.history, [])
                await screen._resume_session(session_id)
                self.assertEqual(screen.runtime.history[0]["content"], "remember this conversation")

    async def test_direct_shell_uses_permission_modal_and_tool_card(self):
        from tui.screens import PermissionModal
        from tui.widgets import ToolCallCard

        app = MiniOpenClawApp(self.runtime_factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            prompt = screen.query_one("#prompt-input")
            prompt.text = "!echo hello"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause(0.05)
                if isinstance(app.screen, PermissionModal):
                    break
            self.assertIsInstance(app.screen, PermissionModal)
            await pilot.press("y")
            for _ in range(60):
                await pilot.pause(0.05)
                if not screen.busy:
                    break
            card = screen.query_one(ToolCallCard)
            self.assertIn("[ok]", str(card.title))
            self.assertTrue(any(item.get("content") == "!echo hello" for item in screen.runtime.history))


if __name__ == "__main__":
    unittest.main()
