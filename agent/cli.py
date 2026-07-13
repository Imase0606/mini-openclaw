"""命令行入口。

用法：
  python -m agent.cli --selfcheck          # Day1：自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"  # Day5 起：真正跑任务（v1 在 Day6）
"""
from __future__ import annotations
import argparse
import sys

from tools.base import build_default_registry
from agent.runtime import AgentRuntime, RuntimeOptions, build_system_prompt


def selfcheck() -> int:
    print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}")
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
        print("[ok] 主循环、停止条件和错误恢复模块可导入")
    except Exception as e:  # noqa
        print(f"[FAIL] 主循环：{e}"); ok = False

    try:
        from backend.multimodal import multimodal_user_content  # noqa
        print("[ok] 图像输入模块可用（--image 内容块通道）")
    except Exception as e:  # noqa
        print(f"[FAIL] 图像输入模块：{e}"); ok = False

    try:
        from agent.memory import KVMemory, Memory  # noqa
        from agent.planning import TodoList  # noqa
        from agent.runtime import AgentRuntime  # noqa
        from agent.tracer import Tracer  # noqa
        print("[ok] 记忆、规划和可观测性模块可导入")
    except Exception as e:  # noqa
        print(f"[FAIL] Day7-Day9 模块：{e}"); ok = False

    try:
        import textual  # noqa
        from tui.app import MiniOpenClawApp  # noqa
        print(f"[ok] Textual TUI 可用（textual {textual.__version__}）")
    except Exception as e:  # noqa
        print(f"[FAIL] Textual TUI：{e}"); ok = False

    print("== 自检", "通过 ✅" if ok else "未通过 ❌", "==")
    print("\nDemo Day 完整验收：python -m eval.demo_check --release")
    return 0 if ok else 1


def confirm_tool_call(name: str, arguments: dict) -> bool:
    """Ask for terminal approval without printing sensitive content payloads."""
    if not sys.stdin.isatty():
        return False
    visible = {
        key: value for key, value in arguments.items()
        if key in {"path", "command", "url", "timeout", "allow_asr", "model_size"}
    }
    if "command" in visible:
        visible["command"] = str(visible["command"])[:200]
    answer = input(f"[权限确认] 允许 {name}({visible})？[y/N] ").strip().lower()
    return answer in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument(
        "--yes",
        action="store_true",
        help="自动批准需确认的工具调用；拒绝规则和沙箱仍然生效",
    )
    p.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="PATH",
        help="附加图片，可重复传入多张；图片最长边会缩放到 1568px",
    )
    p.add_argument(
        "--video-type",
        choices=("auto", "tutorial", "knowledge", "narrative", "commentary", "general"),
        default="auto",
        help="视频知识库模板类型；默认读取转写后自动分类",
    )
    planning = p.add_mutually_exclusive_group()
    planning.add_argument("--plan", action="store_true", help="强制复杂任务先列 Todo 再执行")
    planning.add_argument("--no-plan", action="store_true", help="关闭 Todo 规划，用于简单任务或消融")
    p.add_argument("--max-turns", type=int, default=40, help="Agent 最大决策轮数，默认 40")
    p.add_argument("--no-trace", action="store_true", help="本次不保存运行 trace")
    p.add_argument("--trace-path", metavar="PATH", help="指定 trace JSONL 输出路径")
    p.add_argument("--replay-trace", metavar="PATH", help="回放 trace 并输出 token/成本报告")
    args = p.parse_args(argv)

    if args.replay_trace:
        from agent.tracer import cost_report, replay
        import json
        print(replay(args.replay_trace))
        print(json.dumps(cost_report(args.replay_trace), ensure_ascii=False, indent=2))
        return 0

    if args.selfcheck or (not args.task and not args.image):
        return selfcheck()

    planning_mode = "force" if args.plan else "off" if args.no_plan else "auto"
    runtime = AgentRuntime(
        trace_enabled=not args.no_trace,
        trace_path=args.trace_path,
        confirm_callback=confirm_tool_call,
    )
    if runtime.model_name == "fake-backend":
        print("[提示] 未启用真后端，使用 FakeBackend。配置 DEEPSEEK_API_KEY 后即用真模型。")
    try:
        result = runtime.run_turn(
            args.task or "请描述图片中的内容。",
            options=RuntimeOptions(
                planning_mode=planning_mode,
                video_type=args.video_type,
                max_turns=args.max_turns,
                image_paths=tuple(args.image),
                auto_approve=args.yes,
            ),
        )
        print(result.content)
        returncode = 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary must preserve the trace on backend failure
        from agent.tracer import redact_text

        if runtime.tracer is not None:
            runtime.tracer.record("run", "failed", ok=False, output=f"{type(exc).__name__}: {exc}")
        print(f"[失败] {redact_text(f'{type(exc).__name__}: {exc}', max_chars=1000)}")
        returncode = 1
    finally:
        runtime.close()
    if runtime.tracer is not None:
        import json
        print(f"[trace] {runtime.tracer.path}")
        print("[cost] " + json.dumps(runtime.tracer.summary(), ensure_ascii=False))
    return returncode


if __name__ == "__main__":
    sys.exit(main())
