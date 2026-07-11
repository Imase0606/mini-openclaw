"""Run a real cached-video planning ablation and render a Markdown report."""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent.tracer import cost_report, load_spans


DEFAULT_BVID = "BV1KjoxBoEQJ"
EXTERNAL_ERROR_MARKERS = (
    "402 Payment Required",
    "429 Too Many Requests",
    "500 Internal Server Error",
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Timeout",
)


def classify_outcome(returncode: int, output: str, success: bool) -> str:
    if success:
        return "success"
    if returncode == 124 or "subprocess.TimeoutExpired" in output:
        return "external_error"
    if any(marker in output for marker in EXTERNAL_ERROR_MARKERS):
        return "external_error"
    return "agent_failure"


def run_once(mode: str, index: int, bvid: str, timeout: int, trace_dir: Path) -> dict:
    trace_path = trace_dir / f"ablation-{mode}-{index}.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "agent.cli", "--yes", "--max-turns", "40",
        "--trace-path", str(trace_path),
        "--plan" if mode == "plan" else "--no-plan",
        (
            f"自动判断类型并提炼已有缓存的B站视频 https://www.bilibili.com/video/{bvid}/ "
            "为知识库。必须复用现有 transcript，不要强制重新 ASR；完成后报告生成路径。"
        ),
    ]
    started = time.perf_counter()
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        returncode = process.returncode
        output = (process.stdout or "") + ("\n" + process.stderr if process.stderr else "")
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        output = f"{stdout}\n{stderr}\nsubprocess.TimeoutExpired: {timeout}s"
    elapsed = round(time.perf_counter() - started, 3)
    spans = load_spans(trace_path) if trace_path.is_file() else []
    tools = [span["name"] for span in spans if span.get("kind") == "tool"]
    report = cost_report(trace_path) if spans else {}
    kb = Path("knowledge_base") / bvid
    success = (
        returncode == 0
        and (kb / "index.md").is_file()
        and "达到最大轮数" not in output
        and any(name == "kb_write" for name in tools)
    )
    outcome = classify_outcome(returncode, output, success)
    return {
        "mode": mode,
        "run": index,
        "success": success,
        "outcome": outcome,
        "returncode": returncode,
        "elapsed_seconds": elapsed,
        "llm_steps": sum(span.get("kind") == "llm" for span in spans),
        "tool_steps": len(tools),
        "todo_calls": sum(name in {"todo_write", "update_todo", "insert_todo"} for name in tools),
        "repeat_guards": sum(span.get("name") == "repeat_guard" for span in spans),
        "total_tokens": report.get("total_tokens", 0),
        "estimated_cost_usd": report.get("estimated_cost_usd", 0),
        "trace_path": str(trace_path),
        "output_excerpt": output[-500:],
    }


def average(records: list[dict], key: str) -> float:
    return round(statistics.mean(float(record[key]) for record in records), 3) if records else 0.0


def render_report(records: list[dict], bvid: str) -> str:
    lines = [
        "# 规划层消融实验",
        "",
        f"- 视频：`{bvid}`（复用本地 transcript，不重新 ASR）",
        f"- 日期：{datetime.now(timezone.utc).date().isoformat()}",
        "- 自变量：强制 Todo 规划（`--plan`）/ 关闭规划（`--no-plan`）",
        "- 每组运行次数：3（或命令行指定值）",
        "",
        "## 原始结果",
        "",
        "| 模式 | 运行 | 结果 | 耗时(s) | LLM步 | 工具步 | Todo调用 | 重复阻断 | Token | 成本($) |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for record in records:
        lines.append(
            f"| {record['mode']} | {record['run']} | {record['outcome']} | "
            f"{record['elapsed_seconds']} | {record['llm_steps']} | {record['tool_steps']} | "
            f"{record['todo_calls']} | {record['repeat_guards']} | {record['total_tokens']} | "
            f"{record['estimated_cost_usd']:.6f} |"
        )
    lines.extend(["", "## 汇总", ""])
    for mode in ("plan", "no-plan"):
        group = [record for record in records if record["mode"] == mode]
        valid = [record for record in group if record["outcome"] != "external_error"]
        external = len(group) - len(valid)
        success_rate = sum(record["success"] for record in valid) / max(len(valid), 1)
        lines.append(
            f"- **{mode}**：有效样本 {len(valid)}/{len(group)}，外部错误 {external}，"
            f"有效样本成功率 {success_rate:.0%}，平均耗时 {average(valid, 'elapsed_seconds')}s，"
            f"平均 LLM 步 {average(valid, 'llm_steps')}，平均 Token {average(valid, 'total_tokens')}。"
        )
    lines.extend([
        "",
        "## 解释",
        "",
        "强制规划通常会增加 Todo 相关轮次和 token，但应降低长任务漏步、重复调用和失败后失控的概率。"
        "本实验使用已有缓存的视频流程，主要衡量规划开销。HTTP 402/429/5xx 与超时记为外部错误，"
        "不计入 Agent 成功率；任一组有效样本不足 3 次时，不据此做稳定性因果结论。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--modes", nargs="+", choices=("plan", "no-plan"), default=("plan", "no-plan"))
    parser.add_argument("--bvid", default=DEFAULT_BVID)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--output", default="eval/planning_ablation.md")
    parser.add_argument("--json-output", default=".mini-openclaw/eval/planning_ablation.json")
    parser.add_argument("--trace-dir", default="")
    args = parser.parse_args()
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    trace_dir = Path(args.trace_dir or f".mini-openclaw/eval/{batch_id}")
    trace_dir.mkdir(parents=True, exist_ok=False)
    records = []
    for mode in args.modes:
        for index in range(1, args.runs + 1):
            print(f"[{mode}] run {index}/{args.runs}", flush=True)
            records.append(run_once(mode, index, args.bvid, args.timeout, trace_dir))
    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.output).write_text(render_report(records, args.bvid), encoding="utf-8")
    print(f"wrote {args.output} and {args.json_output}")
    return 0 if all(record["success"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
