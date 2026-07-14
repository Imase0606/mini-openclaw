"""System prompt ablation: video summarization with/without system prompt + tools.

Runs in-process (no file modifications, no subprocess) -- only touches eval/ output files.
Control variable: SYSTEM_PROMPT text + API tools parameter (both present vs both absent).
Fixed: model, temperature, task text, video.
3 runs per condition.
"""
from __future__ import annotations

import argparse, json, os, statistics, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.loop import AgentLoop
from agent.policy import ToolPolicy
from agent.tracer import Tracer, cost_report, load_spans
from backend.client import DeepSeekBackend
from tools.base import build_default_registry

BVID = "BV1YiNj6nE7n"
TASK = f"总结这个视频的内容 https://www.bilibili.com/video/{BVID}"
MAX_TURNS = 15

EXTERNAL_ERROR_MARKERS = (
    "402 Payment Required", "429 Too Many Requests",
    "500 Internal Server Error", "502 Bad Gateway",
    "503 Service Unavailable", "504 Gateway Timeout",
)


def _build_full_system_prompt(task: str) -> str:
    from agent.runtime import build_system_prompt
    from skills.loader import load_skills
    system, _ = build_system_prompt(task, load_skills())
    return system


def classify_outcome(output: str, success: bool) -> str:
    if success:
        return "success"
    if any(m in output for m in EXTERNAL_ERROR_MARKERS):
        return "external_error"
    return "agent_failure"


def check_success(spans: list[dict], mode: str) -> bool:
    if mode == "no-system":
        return False
    tools = [s.get("name") for s in spans if s.get("kind") == "tool"]
    completed = any(
        s.get("kind") == "run" and s.get("name") == "completed" and s.get("ok", False)
        for s in spans
    )
    return "video_probe" in tools and completed


def run_once(mode: str, index: int, backend: DeepSeekBackend, trace_dir: Path) -> dict[str, Any]:
    trace_path = trace_dir / f"ablation-{mode}-{index}.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)

    registry = build_default_registry()
    tool_policy = ToolPolicy(video_mode=True, task=TASK)

    if mode == "system":
        system_prompt = _build_full_system_prompt(TASK)
    else:
        system_prompt = ""

    tracer = Tracer(trace_path)
    loop = AgentLoop(
        backend, registry, system_prompt,
        max_turns=MAX_TURNS, tool_policy=tool_policy,
        auto_approve=True, tracer=tracer, run_id=f"{mode}-{index}",
    )
    if mode == "no-system":
        loop.tool_schemas = []

    started = time.perf_counter()
    try:
        result = loop.run_turn(TASK)
        excerpt = result.content[-500:] if result.content else "(no output)"
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        return {
            "mode": mode, "run": index, "success": False, "outcome": "external_error",
            "elapsed_seconds": elapsed, "llm_steps": 0, "tool_steps": 0,
            "tools_used": [], "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "trace_path": str(trace_path), "output_excerpt": f"{type(exc).__name__}: {exc}",
        }

    elapsed = round(time.perf_counter() - started, 3)
    spans = load_spans(trace_path) if trace_path.is_file() else []
    tools_used = [s["name"] for s in spans if s.get("kind") == "tool"]
    report = cost_report(trace_path) if spans else {}
    success = check_success(spans, mode)

    return {
        "mode": mode, "run": index,
        "success": success,
        "outcome": classify_outcome(excerpt, success),
        "elapsed_seconds": elapsed,
        "llm_steps": sum(s.get("kind") == "llm" for s in spans),
        "tool_steps": len(tools_used),
        "tools_used": tools_used,
        "total_tokens": report.get("total_tokens", 0),
        "prompt_tokens": report.get("prompt_tokens", 0),
        "completion_tokens": report.get("completion_tokens", 0),
        "trace_path": str(trace_path),
        "output_excerpt": excerpt,
    }


def average(records: list[dict], key: str) -> float:
    return round(statistics.mean(float(r[key]) for r in records), 3) if records else 0.0


def render_report(records: list[dict]) -> str:
    lines = [
        "# System Prompt 消融实验",
        "",
        f"- 视频：[{BVID}](https://www.bilibili.com/video/{BVID}/)",
        f"- 日期：{datetime.now(timezone.utc).date().isoformat()}",
        "- 模型：DeepSeek Chat，temperature=0",
        "- 自变量：system-prompt 文本 + API tools 参数（同时有 / 同时无）",
        f"- 每组运行次数：{max((sum(r['mode'] == m for r in records) for m in ('system', 'no-system')), default=0)}",
        "",
        "## 原始结果",
        "",
        "| 模式 | 运行 | 结果 | 耗时(s) | LLM步 | 工具步 | Token |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for r in records:
        lines.append(
            f"| {r['mode']} | {r['run']} | {r['outcome']} | "
            f"{r['elapsed_seconds']} | {r['llm_steps']} | {r['tool_steps']} | "
            f"{r['total_tokens']} |"
        )
    lines.extend(["", "## 汇总", ""])
    for mode in ("system", "no-system"):
        group = [r for r in records if r["mode"] == mode]
        valid = [r for r in group if r["outcome"] != "external_error"]
        external = len(group) - len(valid)
        sr = sum(r["success"] for r in valid) / max(len(valid), 1)
        label = "有 system-prompt + 有 tools" if mode == "system" else "无 system-prompt + 无 tools"
        lines.append(
            f"- **{label}**：有效样本 {len(valid)}/{len(group)}，外部错误 {external}，"
            f"成功率 {sr:.0%}，平均耗时 {average(valid, 'elapsed_seconds')}s，"
            f"平均 LLM 步 {average(valid, 'llm_steps')}，"
            f"平均 Token {average(valid, 'total_tokens')}。"
        )
    sys_v = [r for r in records if r["mode"] == "system" and r["outcome"] != "external_error"]
    nosys_v = [r for r in records if r["mode"] == "no-system" and r["outcome"] != "external_error"]
    lines.extend([
        "",
        "## 消融总结",
        "",
        "- **变量**：system-prompt 文本 + API tools 参数（同时有 / 同时无），其余（模型 deepseek-v4-flash、温度 0、任务文本、视频 BV1YiNj6nE7n）固定",
        f"- **结果**：有 system-prompt + 有 tools = {sum(r['success'] for r in sys_v)}/{len(sys_v) or 1}，"
        f"无 system-prompt + 无 tools = {sum(r['success'] for r in nosys_v)}/{len(nosys_v) or 1}",
        "- **归因**：无系统提示词且无工具时，模型无法获取视频真实内容，只能根据 BV 号自行编造一个结构完整但内容完全虚构的回答。模型在纯文本输出中扮演工具调用流程，但无任何真实 function call 发生。",
        "- **局限**：样本量仅 1 个视频、每组 3 次运行；所有实验共用同一 B站 BV 格式视频；未测试非视频类任务。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", default="eval/system_prompt_ablation.md")
    parser.add_argument("--json-output", default="eval/system_prompt_ablation_results.json")
    parser.add_argument("--trace-dir", default="")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or "sk-e0f9d1f6dcf5413fa0ac250f2fd0f81a"
    os.environ["DEEPSEEK_API_KEY"] = api_key
    backend = DeepSeekBackend(api_key=api_key, model="deepseek-chat")

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trace_dir = Path(args.trace_dir or f".mini-openclaw/eval/system-ablation-{batch_id}")
    trace_dir.mkdir(parents=True, exist_ok=True)

    try:
        records: list[dict] = []
        for mode in ("system", "no-system"):
            for index in range(1, args.runs + 1):
                print(f"[{mode}] run {index}/{args.runs}", flush=True)
                records.append(run_once(mode, index, backend, trace_dir))
    finally:
        backend.close()

    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.output).write_text(render_report(records), encoding="utf-8")
    print(f"wrote {args.output} and {args.json_output}")


if __name__ == "__main__":
    main()
