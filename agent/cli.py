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
from skills.loader import load_skills, match_skills, skills_catalog


def build_system_prompt(task: str, skills) -> tuple[str, list[str]]:
    """Build the hybrid Skill catalog and preload high-confidence matches."""
    matched = match_skills(task, skills)
    matched_names = sorted(skill.name for skill in matched)
    system = SYSTEM_PROMPT + "\n\n# 可用 Skills（混合按需加载）\n" + skills_catalog(skills)
    if matched:
        bodies = "\n\n---\n\n".join(
            f"## Skill: {skill.name}\n{skill.body}" for skill in matched
        )
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
    return system, matched_names


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

    try:
        from backend.multimodal import multimodal_user_content  # noqa
        print("[ok] 图像输入模块可用（--image 内容块通道）")
    except Exception as e:  # noqa
        print(f"[FAIL] 图像输入模块：{e}"); ok = False

    print("== 自检", "通过 ✅" if ok else "未通过 ❌", "==")
    print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="PATH",
        help="附加图片，可重复传入多张；图片最长边会缩放到 1568px",
    )
    args = p.parse_args(argv)

    if args.selfcheck or (not args.task and not args.image):
        return selfcheck()

    user_task: str | list[dict[str, object]] = args.task or "请描述图片中的内容。"
    if args.image:
        from backend.multimodal import multimodal_user_content
        try:
            user_task = multimodal_user_content(str(user_task), args.image)
        except (OSError, ValueError) as exc:
            print(f"[失败] 无法准备图片输入：{exc}")
            return 2

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
        backend_options = {}
        if args.image:
            backend_options = {
                "api_key": os.environ.get("VISION_API_KEY") or None,
                "base_url": os.environ.get("VISION_BASE_URL") or None,
                "model": os.environ.get("VISION_MODEL") or None,
            }
        backend = DeepSeekBackend(**backend_options)
        if args.image and not os.environ.get("VISION_MODEL"):
            print("[提示] 未配置 VISION_MODEL，将使用 DEEPSEEK_MODEL；该模型必须支持视觉输入。")
    except Exception as e:  # noqa
        from backend.fake_backend import FakeBackend
        print(f"[提示] 未启用真后端（{e}），回退 FakeBackend。配置 DEEPSEEK_API_KEY 后即用真模型。")
        backend = FakeBackend()
    skills = load_skills()
    system, _matched_names = build_system_prompt(args.task or "", skills)

    agent = AgentLoop(backend, reg, system)
    try:
        print(agent.run(user_task))
        return 0
    except RuntimeError as exc:
        if args.image:
            print(f"[失败] {exc}")
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
