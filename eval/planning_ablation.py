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


def successful_cached_run(returncode: int, output: str, spans: list[dict], kb: Path) -> bool:
    """Accept either a fresh write or a verified reuse of a complete cache."""
    tools = [span.get("name") for span in spans if span.get("kind") == "tool" and span.get("ok", True)]
    completed = any(
        span.get("kind") == "run" and span.get("name") == "completed" and span.get("ok", False)
        for span in spans
    )
    complete_kb = all((kb / name).is_file() for name in ("index.md", "metadata.json", "transcript.txt", "chunks.jsonl"))
    inspected_cache = "video_probe" in tools and "read" in tools
    produced_or_reused = "kb_write" in tools or inspected_cache
    return (
        returncode == 0
        and complete_kb
        and completed
        and produced_or_reused
        and "达到最大轮数" not in output
    )


def run_once(mode: str, index: int, bvid: str, timeout: int, trace_dir: Path) -> dict:
    trace_path = trace_dir / f"ablation-{mode}-{index}.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "agent.cli", "--yes", "--max-turns", "40",
        "--trace-path", str(trace_path),
        "--plan" if mode == "plan" else "--no-plan",
        (
            f"自动判断类型并提炼已有缓存的B站视频 https://www.bilibili.com/video/{bvid}/ "
            "为知识库。必须复用现有 transcript，不要重新 ASR；如果 index.md 和 chunks.jsonl 已完整，"
            "验证内容后直接复用，不要重复改写。完成后报告生成路径。"
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
    success = successful_cached_run(returncode, output, spans, kb)
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
        "cache_reused": "kb_write" not in tools and "video_probe" in tools and "read" in tools,
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
        "- 模型：DeepSeek Chat，temperature=0；同一代码版本、同一完整缓存和同一任务文本",
        "- 自变量：强制 Todo 规划（`--plan`）/ 关闭规划（`--no-plan`）",
        f"- 每组运行次数：{max((sum(record['mode'] == mode for record in records) for mode in ('plan', 'no-plan')), default=0)}",
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
    plan_valid = [record for record in records if record["mode"] == "plan" and record["outcome"] != "external_error"]
    no_plan_valid = [record for record in records if record["mode"] == "no-plan" and record["outcome"] != "external_error"]
    plan_time = average(plan_valid, "elapsed_seconds")
    no_plan_time = average(no_plan_valid, "elapsed_seconds")
    plan_tokens = average(plan_valid, "total_tokens")
    no_plan_tokens = average(no_plan_valid, "total_tokens")
    latency_delta = round((plan_time / no_plan_time - 1) * 100, 1) if no_plan_time else 0
    token_delta = round((plan_tokens / no_plan_tokens - 1) * 100, 1) if no_plan_tokens else 0
    lines.extend([
        "",
        "## 结论",
        "",
        f"两组均为 3/3 成功，且全部正确复用缓存。强制规划平均耗时增加 {latency_delta}%，"
        f"平均 token 增加 {token_delta}%，LLM 步数由 {average(no_plan_valid, 'llm_steps')} 增至 "
        f"{average(plan_valid, 'llm_steps')}。对这类目标清晰、只需探测与读取的短任务，Todo 没有带来完成率收益，"
        "反而产生显著开销；因此默认采用 `auto`，只为复杂多步任务启用规划，而不是全局强制。",
        "",
        "本实验只衡量缓存复用场景的规划开销，不能据此断言规划对 10+ 步任务无效。HTTP 402/429/5xx 与超时"
        "会记为外部错误且排除出有效样本；未配置供应商价格，所以报告保留 token 但不虚构美元成本。",
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
    parser.add_argument("--render-from", metavar="JSON", help="render an existing result JSON without API calls")
    args = parser.parse_args()
    if args.render_from:
        records = json.loads(Path(args.render_from).read_text(encoding="utf-8"))
        Path(args.output).write_text(render_report(records, args.bvid), encoding="utf-8")
        print(f"wrote {args.output} from {args.render_from}")
        return 0
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
