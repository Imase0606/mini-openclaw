"""One-command Demo Day readiness checks.

The default mode is offline and exercises the runtime layers used on stage.
``--release`` adds submission gates that depend on tracked evaluation evidence
and Git milestone tags.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from agent.context import compact_history, estimate_tokens
from agent.loop import AgentLoop
from agent.memory import KVMemory, Memory
from agent.planning import TodoList
from agent.policy import ToolPolicy
from agent.runtime import load_model_profiles
from agent.session import SessionStore
from agent.tracer import Tracer, replay
from backend.fake_backend import FakeBackend
from security.redteam import run_cases
from eval.teacher_acceptance import run_offline as run_teacher_acceptance
from skills.loader import load_skills, match_skills
from tools.base import Tool, ToolRegistry, build_default_registry
from tools.mcp_client import MCPClient
from tools.memory import register_memory_tools
from tools.planning import register_planning_tools


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TAGS = {"v1", "v3", "final"}
REQUIRED_MODULE_DOCS = (
    "agent/README.md",
    "backend/README.md",
    "tools/README.md",
    "mcp/README.md",
    "prompt/README.md",
    "skills/README.md",
    "security/README.md",
    "eval/README.md",
    "tui/README.md",
)


@dataclass(frozen=True)
class Check:
    section: str
    name: str
    ok: bool
    detail: str


class SequenceBackend:
    """Small deterministic backend used to prove tool-error recovery."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)

    def chat(self, _messages: list[dict[str, Any]], tools: list[dict] | None = None) -> dict[str, Any]:  # noqa: ARG002
        response = self.responses.pop(0)
        return {
            "role": "assistant",
            "content": response.get("content", ""),
            "tool_calls": response.get("tool_calls", []),
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        }


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _tool_call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [{"id": f"demo-{name}", "name": name, "arguments": arguments}],
    }


def _check_core_tools() -> tuple[bool, str]:
    registry = build_default_registry()
    required = {"read", "write", "edit", "grep", "glob", "bash"}
    if not required.issubset(registry.names()):
        return False, f"missing: {sorted(required - set(registry.names()))}"
    with tempfile.TemporaryDirectory(prefix="mini-openclaw-demo-") as tmp:
        root = Path(tmp)
        with working_directory(root):
            write = registry.get("write")
            edit = registry.get("edit")
            read = registry.get("read")
            grep = registry.get("grep")
            glob = registry.get("glob")
            bash = registry.get("bash")
            assert all((write, edit, read, grep, glob, bash))
            write.run(path="sample.txt", content="alpha\nbeta\n")
            edit_result = edit.run(path="sample.txt", old="beta", new="gamma")
            read_result = read.run(path="sample.txt")
            grep_result = grep.run(pattern="gamma", path="sample.txt")
            glob_result = glob.run(pattern="sample.txt")
            bash_result = bash.run(command="echo demo-ready")
    ok = all((
        "完成 1 处替换" in edit_result,
        "gamma" in read_result,
        "gamma" in grep_result,
        "sample.txt" in glob_result,
        "demo-ready" in bash_result,
    ))
    return ok, "read/write/edit/grep/glob/bash"


def _check_recovery() -> tuple[bool, str]:
    attempts = {"count": 0}

    def flaky_read(**_arguments: Any) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("simulated transient failure")
        return "recovered"

    registry = ToolRegistry()
    registry.register(Tool("read", "read", {"type": "object"}, flaky_read))
    backend = SequenceBackend([_tool_call("read", path="fixture.txt"), {"content": "done"}])
    result = AgentLoop(backend, registry, "system", tool_policy=ToolPolicy()).run_turn("recover")
    return result.status == "completed" and attempts["count"] == 3, f"attempts={attempts['count']}"


def _check_compaction() -> tuple[bool, str]:
    history: list[dict[str, Any]] = []
    for index in range(8):
        history.extend((
            {"role": "user", "content": f"request-{index} " + "x" * 800},
            {"role": "assistant", "content": f"answer-{index} " + "y" * 800},
        ))
    before = estimate_tokens(history)
    compacted = compact_history(history, FakeBackend(), keep_rounds=2)
    after = estimate_tokens(compacted)
    has_memo = any(item.get("_history_memo") for item in compacted)
    return has_memo and after < before, f"estimated tokens {before} -> {after}"


def _check_bilibili_auth() -> tuple[bool, str]:
    try:
        from tools.bilibili_auth import render_qr_ascii, session_path

        rendered = render_qr_ascii("https://example.invalid/short-lived-qr")
        outside_workspace = not session_path().resolve().is_relative_to(ROOT.resolve())
        return bool(rendered.strip()) and outside_workspace, "QR ready; credentials outside workspace"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _check_documents() -> tuple[bool, str]:
    missing = [path for path in REQUIRED_MODULE_DOCS if not (ROOT / path).is_file()]
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    required_topics = ("安全", "可观测", "成本", "compaction", "MCP", "Skill")
    missing_topics = [topic for topic in required_topics if topic not in architecture]
    ok = not missing and not missing_topics and (ROOT / "docs/demo_runbook.md").is_file()
    detail = "complete" if ok else f"missing files={missing}; missing topics={missing_topics}"
    return ok, detail


def _check_docker_context() -> tuple[bool, str]:
    path = ROOT / ".dockerignore"
    dockerfile = ROOT / "Dockerfile"
    if not path.is_file():
        return False, "missing .dockerignore"
    if not dockerfile.is_file():
        return False, "missing Dockerfile"
    entries = {
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required = {".git/", ".mini-openclaw/", "knowledge_base/*", "*.zip", "__pycache__/"}
    missing = sorted(required - entries)
    model_excluded = any(entry.rstrip("/") == "models" for entry in entries if not entry.startswith("!"))
    ephemeral_auth = "BILIBILI_AUTH_MODE=ephemeral" in dockerfile.read_text(encoding="utf-8")
    ok = not missing and not model_excluded and ephemeral_auth
    detail = "runtime state excluded; bundled model retained; Bilibili auth ephemeral" if ok else (
        f"missing={missing}; models_excluded={model_excluded}; ephemeral_auth={ephemeral_auth}"
    )
    return ok, detail


def _check_ablation() -> tuple[bool, str]:
    path = ROOT / "eval/planning_ablation_results.json"
    if not path.is_file():
        return False, "missing tracked eval/planning_ablation_results.json"
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid JSON: {exc}"
    counts = {
        mode: sum(record.get("mode") == mode and record.get("outcome") == "success" for record in records)
        for mode in ("plan", "no-plan")
    }
    return min(counts.values(), default=0) >= 3, f"successful samples: {counts}"


def _run_git(*arguments: str) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if process.returncode == 0 or os.name == "nt" or not (ROOT / ".git").is_file():
        return process
    pointer = (ROOT / ".git").read_text(encoding="utf-8", errors="replace").strip()
    match = re.fullmatch(r"gitdir:\s*([A-Za-z]):[\\/](.+)", pointer)
    if not match:
        return process
    git_dir = Path(f"/mnt/{match.group(1).lower()}/{match.group(2).replace(chr(92), '/')}")
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={ROOT}", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )


def _check_tags() -> tuple[bool, str]:
    process = _run_git("tag", "--list")
    tags = {line.strip() for line in process.stdout.splitlines() if line.strip()}
    if process.returncode != 0:
        return False, (process.stderr or "git tag failed").strip()
    missing = sorted(REQUIRED_TAGS - tags)
    return not missing, "present" if not missing else f"missing: {missing}"


def _check_live_model(alias: str, *, with_image: bool = False) -> tuple[bool, str]:
    from backend.client import DeepSeekBackend

    profile = load_model_profiles()[alias]
    if not profile.configured:
        return False, f"{profile.api_key_env} is not set"
    backend = DeepSeekBackend(
        api_key=os.environ[profile.api_key_env],
        base_url=os.environ.get(profile.base_url_env) or profile.default_base_url,
        model=os.environ.get(profile.model_env) or profile.default_model,
        timeout=60,
    )
    try:
        if with_image:
            from PIL import Image, ImageDraw
            from backend.multimodal import multimodal_user_content

            with tempfile.TemporaryDirectory() as tmp:
                image_path = Path(tmp) / "vision-check.png"
                image = Image.new("RGB", (320, 120), "white")
                ImageDraw.Draw(image).text((30, 35), "DEMO 42", fill="black")
                image.save(image_path)
                content = multimodal_user_content("Read the number in this image. Reply with the number only.", (str(image_path),))
                response = backend.chat([{"role": "user", "content": content}], tools=[])
                answer = str(response.get("content") or "").strip()
                ok = "42" in answer
        else:
            response = backend.chat(
                [{"role": "user", "content": "Reply with exactly DEMO_READY."}],
                tools=[],
            )
            answer = str(response.get("content") or "").strip()
            ok = "DEMO_READY" in answer
        tokens = int((response.get("usage") or {}).get("total_tokens") or 0)
        return ok and tokens > 0, f"model={profile.default_model}, tokens={tokens}, answer={answer[:40]!r}"
    except Exception as exc:  # noqa: BLE001 - live readiness must report and continue
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        backend.close()


def _check_live_filesystem_mcp() -> tuple[bool, str]:
    client = MCPClient(
        ["npx", "-y", "@modelcontextprotocol/server-filesystem", str(ROOT)],
        name="filesystem-live",
        startup_timeout=30,
    )
    try:
        client.start()
        names = [tool.get("name", "") for tool in client.list_tools()]
        return bool(names) and any("read" in name for name in names), f"{len(names)} tools"
    except Exception as exc:  # noqa: BLE001 - live readiness must report and continue
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        client.close()


def run_checks(*, release: bool = False, live: bool = False) -> list[Check]:
    checks: list[Check] = []

    def add(section: str, name: str, outcome: tuple[bool, str]) -> None:
        checks.append(Check(section, name, outcome[0], outcome[1]))

    registry = build_default_registry()
    todo = TodoList()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runtime_memory = KVMemory(root / "memory.json")
        register_memory_tools(registry, runtime_memory)
        register_planning_tools(registry, todo)
        expected = {
            "remember", "forget_memory", "recall_memory", "todo_write", "update_todo",
            "kb_search", "kb_catalog", "kb_forget", "kb_restore", "kb_export", "kb_purge_trash",
        }
        add("A", "工具注册", (expected.issubset(set(registry.names())), f"{len(registry)} tools"))

        project_memory = Memory(root / "MEMORY.md")
        project_memory.write("演示时间戳使用 UTC")
        runtime_memory.remember("video_style", "教程视频保留步骤")
        memory_ok = "UTC" in Memory(root / "MEMORY.md").recall() and "教程" in KVMemory(root / "memory.json").recall("video")
        add("E", "跨会话记忆", (memory_ok, "Markdown + atomic JSON"))

        todo.write(["探测视频", "读取转写", "生成知识库"])
        todo.update(1, "completed")
        add("C", "规划状态机", ("[x] 1" in todo.render() and "[ ] 2" in todo.render(), todo.render().replace("\n", " | ")))

        tracer = Tracer(root / "trace.jsonl")
        tracer.record("llm", "demo", usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}, output="ok")
        trace_ok = "demo" in replay(tracer.path) and tracer.summary()["total_tokens"] == 12
        add("G", "Trace 与 token", (trace_ok, "12 tokens, replayable"))

        sessions = SessionStore(root / "sessions", workdir=root)
        sessions.save("demo", [{"role": "user", "content": "hello"}], settings={"model_alias": "deepseek"})
        restored = sessions.load("demo")
        add("E", "TUI 会话恢复", (restored.history[0]["content"] == "hello", "redacted JSON session"))

    add("C", "核心工具实跑", _check_core_tools())
    add("E", "瞬时失败恢复", _check_recovery())
    add("E", "上下文压缩", _check_compaction())

    skills = load_skills()
    matched = [skill.name for skill in match_skills("总结 B站视频 BV1DEMO", skills)]
    add("D", "Skill 召回", (matched == ["video-summary"], str(matched)))
    personal = [skill.name for skill in match_skills("从我之前提炼的视频里找安装方法", skills)]
    add("D", "个人知识 Skill", (personal == ["personal-video-knowledge"], str(personal)))
    manager = [skill.name for skill in match_skills("导出知识库并查看回收区", skills)]
    add("D", "知识治理 Skill", (manager == ["personal-video-knowledge-manager"], str(manager)))

    client = MCPClient([sys.executable, str(ROOT / "mcp/echo_server.py")], name="demo-echo", startup_timeout=5)
    try:
        client.start()
        add("D", "MCP stdio", (len(client.list_tools()) >= 1, "echo server"))
    except Exception as exc:  # noqa: BLE001 - readiness report must continue
        add("D", "MCP stdio", (False, f"{type(exc).__name__}: {exc}"))
    finally:
        client.close()

    redteam = run_cases(ROOT)
    add("F", "安全红队", (all(result.passed for result in redteam), f"{sum(result.passed for result in redteam)}/{len(redteam)}"))

    teacher = run_teacher_acceptance()
    teacher_passed = sum(bool(item["passed"]) for item in teacher)
    add("B", "教师视频验收", (teacher_passed == len(teacher), f"{teacher_passed}/{len(teacher)}"))
    add("D", "B站扫码字幕", _check_bilibili_auth())

    candidates = [ROOT / "knowledge_base/BV1KjoxBoEQJ", ROOT / "knowledge_base/BV1j9MP6wEV9"]
    kb = next((path for path in candidates if (path / "transcript.txt").is_file()), None)
    kb_ok = False
    detail = "no cached sample"
    if kb is not None:
        try:
            chunks = [json.loads(line) for line in (kb / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line]
            kb_ok = (kb / "index.md").is_file() and (kb / "metadata.json").is_file() and bool(chunks)
            detail = f"{kb.name}: {len(chunks)} chunks"
        except (OSError, json.JSONDecodeError) as exc:
            detail = str(exc)
    add("B", "视频知识库样例", (kb_ok, detail))

    try:
        import textual
        from tui.app import MiniOpenClawApp  # noqa: F401

        add("A", "交互式 TUI", (True, f"Textual {textual.__version__}"))
    except Exception as exc:  # noqa: BLE001 - readiness report must continue
        add("A", "交互式 TUI", (False, str(exc)))

    profiles = load_model_profiles()
    deepseek_ok = "deepseek" in profiles and profiles["deepseek"].configured
    add("A", "DeepSeek 配置", (deepseek_ok, "configured" if deepseek_ok else "DEEPSEEK_API_KEY is not set"))
    mimo_ok = "mimo" in profiles and profiles["mimo"].configured and profiles["mimo"].supports_images
    add("A", "MiMo 视觉加分项", (mimo_ok, "configured" if mimo_ok else "optional model not configured"))
    assets = [ROOT / "tui/assets/knowledge-terminal-128.png", ROOT / "tui/assets/knowledge-terminal-512.png"]
    add("A", "项目形象资产", (all(path.is_file() for path in assets), ", ".join(str(path.relative_to(ROOT)) for path in assets)))
    add("H", "技术文档", _check_documents())

    if live:
        add("A", "DeepSeek 实时请求", _check_live_model("deepseek"))
        add("A", "MiMo 实时视觉", _check_live_model("mimo", with_image=True))
        add("D", "filesystem MCP 实时握手", _check_live_filesystem_mcp())

    if release:
        add("H", "消融数据", _check_ablation())
        add("H", "里程碑 tags", _check_tags())
        add("H", "构建上下文隔离", _check_docker_context())
        # The shared Windows/WSL worktree is checked out with CRLF files.
        # Pin normalization so WSL Git does not report every text file dirty.
        status = _run_git("-c", "core.autocrlf=true", "status", "--porcelain")
        clean = status.stdout.strip()
        status_ok = status.returncode == 0 and not clean
        status_detail = (
            "clean" if status_ok else (status.stderr.strip() if status.returncode else "uncommitted changes present")
        )
        add("H", "发布工作树", (status_ok, status_detail))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="also validate tracked evidence, tags, and clean Git state")
    parser.add_argument("--live", action="store_true", help="make minimal DeepSeek/MiMo requests and start filesystem MCP")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    checks = run_checks(release=args.release, live=args.live)
    if args.json:
        print(json.dumps([check.__dict__ for check in checks], ensure_ascii=False, indent=2))
    else:
        for check in checks:
            print(f"[{'ok' if check.ok else 'FAIL'}] {check.section} {check.name}: {check.detail}")
        passed = sum(check.ok for check in checks)
        print(f"demo_check: {passed}/{len(checks)} passed")
        if not args.release:
            print("release gates: python -m eval.demo_check --release")
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
