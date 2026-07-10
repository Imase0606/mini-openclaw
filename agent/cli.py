"""命令行入口。

用法：
  python -m agent.cli --selfcheck          # Day1：自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"  # Day5 起：真正跑任务（v1 在 Day6）
"""
from __future__ import annotations
import argparse
import os
import sys

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT
from skills.loader import load_skills, skills_catalog, Skill


def selfcheck() -> int:
    print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}（Day5 起会变多）")
    except Exception as e:  # noqa
        print(f"[FAIL] 工具注册表：{e}"); ok = False

    try:
        from backend.fake_backend import FakeBackend
        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        print("[ok] FakeBackend 可用（未配 DEEPSEEK_API_KEY 时的离线占位后端）")
    except Exception as e:  # noqa
        print(f"[FAIL] FakeBackend：{e}"); ok = False

    try:
        from agent.loop import AgentLoop  # noqa
        print("[ok] 主循环模块可导入（Day5 实现 run 逻辑）")
    except Exception as e:  # noqa
        print(f"[FAIL] 主循环：{e}"); ok = False

    print("== 自检", "通过 ✅" if ok else "未通过 ❌", "==")
    print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")
    return 0 if ok else 1


def _match_skills(task: str, skills: list[Skill]) -> list[Skill]:
    """按需召回：任务命中某 skill 的 description 或 name 中的关键词时返回这些 skill。"""
    task_lower = task.lower()
    matched: list[Skill] = []
    for s in skills:
        full_keywords: set[str] = set()   # full-token matches (strong signal)
        cjk_grams: set[str] = set()       # 2-gram substrings (auxiliary, short CJK tokens only)
        for field in (s.name, s.description):
            for token in field.lower().replace("-", " ").replace("_", " ").split():
                t = token.strip(",.，。!！?？:：;；()（）[]【】""''""、/")
                if len(t) >= 2:
                    full_keywords.add(t)
                    # Only extract 2-grams from short CJK tokens (< 8 chars);
                    # long CJK strings (whole sentences) otherwise generate too many common substrings
                    if len(t) < 8 and any("一" <= c <= "鿿" for c in t):
                        for i in range(len(t) - 1):
                            cjk_grams.add(t[i:i + 2])
        # Require at least one strong full-token match; 2-grams alone are not enough
        has_full = any(kw in task_lower for kw in full_keywords)
        has_cjk_gram = any(g in task_lower for g in cjk_grams)
        if has_full or has_cjk_gram:
            matched.append(s)
    return matched


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    args = p.parse_args(argv)

    if args.selfcheck or not args.task:
        return selfcheck()

    # 真正跑任务：优先用 DeepSeek API；没配 key 时回退到 FakeBackend（离线打通管道）
    from agent.loop import AgentLoop
    reg = build_default_registry()
    from mcp.client import MCPClient, register_mcp_tools
    # 1) Echo server（MCP 测试用）
    try:
        mcp_echo = MCPClient(["python", "mcp/echo_server.py"], name="echo")
        mcp_echo.start()
        register_mcp_tools(reg, mcp_echo)
    except Exception as e:  # noqa
        print(f"[提示] MCP echo server 未接入（{e}）。")

    # 2) 官方 filesystem server（通过 npx），配 MCP_FS_DIR 环境变量指定允许的目录
    fs_dir = os.environ.get("MCP_FS_DIR", ".")
    try:
        mcp_fs = MCPClient(
            ["npx", "-y", "@modelcontextprotocol/server-filesystem", fs_dir],
            name="filesystem",
        )
        mcp_fs.start()
        register_mcp_tools(reg, mcp_fs)
        print(f"[MCP] filesystem server 已接入（允许目录：{fs_dir}）")
    except Exception as e:  # noqa
        # npx 或 Node 不可用时，退到自写 calc server
        print(f"[提示] filesystem server 未接入（{e}），尝试 calc server...")
        try:
            mcp_calc = MCPClient(["python", "mcp/calc_server.py"], name="calc")
            mcp_calc.start()
            register_mcp_tools(reg, mcp_calc)
            print("[MCP] calc server 已接入（add / multiply）")
        except Exception as e2:  # noqa
            print(f"[提示] calc server 也未接入（{e2}），仅用内置工具。")
    try:
        from backend.client import DeepSeekBackend
        backend = DeepSeekBackend()                       # 需要 DEEPSEEK_API_KEY
    except Exception as e:  # noqa
        from backend.fake_backend import FakeBackend
        print(f"[提示] 未启用真后端（{e}），回退 FakeBackend。配置 DEEPSEEK_API_KEY 后即用真模型。")
        backend = FakeBackend()
    # --- Skill 按需注入（Day9 Step 2）---
    skills = load_skills()
    system = SYSTEM_PROMPT + "\n\n# 可用 Skills（按名称/描述匹配后激活流程）\n" + skills_catalog(skills)
    matched = _match_skills(args.task, skills)
    if matched:
        bodies = "\n\n---\n\n".join(f"## Skill: {s.name}\n{s.body}" for s in matched)
        system += "\n\n# 当前任务激活的 Skill 流程\n" + bodies

    agent = AgentLoop(backend, reg, system)
    print(agent.run(args.task))
    return 0


if __name__ == "__main__":
    sys.exit(main())
