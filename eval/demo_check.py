"""Offline one-command Demo Day readiness check."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from agent.memory import KVMemory, Memory
from agent.planning import TodoList
from agent.tracer import Tracer, replay
from mcp.client import MCPClient
from security.redteam import run_cases
from skills.loader import load_skills, match_skills
from tools.base import build_default_registry
from tools.memory import register_memory_tools
from tools.planning import register_planning_tools


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    registry = build_default_registry()
    todo = TodoList()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runtime_memory = KVMemory(root / "memory.json")
        register_memory_tools(registry, runtime_memory)
        register_planning_tools(registry, todo)
        expected = {"remember", "forget_memory", "recall_memory", "todo_write", "update_todo"}
        checks.append(("工具注册", expected.issubset(set(registry.names())), f"{len(registry)} tools"))

        project_memory = Memory(root / "MEMORY.md")
        project_memory.write("演示时间戳使用 UTC")
        runtime_memory.remember("video_style", "教程视频保留步骤")
        memory_ok = "UTC" in Memory(root / "MEMORY.md").recall() and "教程" in KVMemory(root / "memory.json").recall("video")
        checks.append(("跨会话记忆", memory_ok, "Markdown + JSON"))

        todo.write(["探测视频", "读取转写", "生成知识库"])
        todo.update(1, "completed")
        checks.append(("规划状态机", "[x] 1" in todo.render() and "[ ] 2" in todo.render(), todo.render().replace("\n", " | ")))

        tracer = Tracer(root / "trace.jsonl")
        tracer.record("llm", "demo", usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}, output="ok")
        checks.append(("Trace 回放", "demo" in replay(tracer.path) and tracer.summary()["total_tokens"] == 12, str(tracer.path)))

    skills = load_skills()
    matched = [skill.name for skill in match_skills("总结 B站视频 BV1DEMO", skills)]
    checks.append(("Skill 召回", matched == ["video-summary"], str(matched)))

    client = MCPClient([sys.executable, "mcp/echo_server.py"], name="demo-echo", startup_timeout=5)
    try:
        client.start()
        checks.append(("MCP", len(client.list_tools()) >= 1, "echo server"))
    except Exception as exc:
        checks.append(("MCP", False, str(exc)))
    finally:
        client.close()

    redteam = run_cases()
    checks.append(("安全红队", all(result.passed for result in redteam), f"{sum(result.passed for result in redteam)}/{len(redteam)}"))

    candidates = [Path("knowledge_base/BV1KjoxBoEQJ"), Path("knowledge_base/BV1j9MP6wEV9")]
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
    checks.append(("视频知识库样例", kb_ok, detail))

    for name, ok, detail in checks:
        print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}")
    passed = sum(ok for _name, ok, _detail in checks)
    print(f"demo_check: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
