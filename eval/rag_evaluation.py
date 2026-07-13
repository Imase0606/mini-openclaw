"""Deterministic offline evaluation for personal video knowledge retrieval."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from tools.knowledge import (
    INDEX_PATH,
    KB_ROOT,
    _connect,
    rebuild_index,
    search_knowledge,
    tokenize,
)


FIXTURE_ROOT = Path(__file__).with_name("fixtures")
WORKSPACE_CASES = [
    {"query": "Claude Code Windows 安装环境变量", "expected_bvids": ["BV1KjoxBoEQJ"]},
    {"query": "玻璃 吉他弹唱 教学", "expected_bvids": ["BV17PGi68EAU"]},
    {"query": "文班亚马 篮球 老将", "expected_bvids": ["BV1SYTF6KECF"]},
    {"query": "量子潜水艇维修手册", "expected_bvids": []},
]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _load_cases(path: Path = FIXTURE_ROOT / "rag_cases.json") -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8")))


def _build_fixture(root: Path) -> None:
    videos = json.loads((FIXTURE_ROOT / "rag_videos.json").read_text(encoding="utf-8"))
    for video in videos:
        job = root / video["bvid"]
        job.mkdir(parents=True)
        metadata = {
            key: video[key]
            for key in ("bvid", "title", "author", "video_type")
        }
        metadata["source_url"] = f"https://www.bilibili.com/video/{video['bvid']}/"
        metadata["duration"] = 100
        job.joinpath("metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False),
            encoding="utf-8",
        )
        job.joinpath("transcript.txt").write_text(video["transcript"] + "\n", encoding="utf-8")
        job.joinpath("index.md").write_text(f"# {video['title']}\n", encoding="utf-8")


def evaluate(
    *,
    kb_root: Path,
    index_path: Path,
    cases: list[dict[str, Any]],
    top_k: int = 6,
) -> dict[str, Any]:
    records = []
    recalls = []
    reciprocal_ranks = []
    ndcgs = []
    no_answer_hits = 0
    no_answer_cases = 0
    injected_chars = 0
    citation_valid = citation_total = 0
    diversity_scores = []
    latencies = []

    for case in cases:
        started = time.perf_counter()
        result = search_knowledge(
            case["query"],
            top_k=top_k,
            kb_root=kb_root,
            index_path=index_path,
        )
        latencies.append((time.perf_counter() - started) * 1000)
        results = result.get("results", [])
        injected_chars += sum(len(item.get("text", "")) for item in results)
        returned = []
        for item in results:
            if item["bvid"] not in returned:
                returned.append(item["bvid"])
            citation_total += 1
            citation_valid += int(bool(item.get("citation") and item.get("playback_url")))
        if results:
            diversity_scores.append(len(set(item["bvid"] for item in results)) / len(results))

        expected = set(case.get("expected_bvids") or [])
        if not expected:
            correct = not results
            no_answer_cases += 1
            no_answer_hits += int(correct)
            rank = None
            recall = None
            ndcg = None
        else:
            relevant_returned = expected & set(returned)
            recall = len(relevant_returned) / len(expected)
            recalls.append(recall)
            rank = next((index for index, bvid in enumerate(returned, 1) if bvid in expected), None)
            reciprocal_ranks.append(1 / rank if rank else 0.0)
            dcg = sum(
                1 / math.log2(index + 1)
                for index, bvid in enumerate(returned, 1)
                if bvid in expected
            )
            ideal_hits = min(len(expected), top_k)
            idcg = sum(1 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
            ndcg = dcg / max(idcg, 1e-9)
            ndcgs.append(ndcg)
            correct = recall == 1.0
        records.append({
            "query": case["query"],
            "expected_bvids": sorted(expected),
            "returned_bvids": returned,
            "rank": rank,
            "recall": recall,
            "ndcg": round(ndcg, 4) if ndcg is not None else None,
            "correct": correct,
        })

    transcript_bytes = sum(path.stat().st_size for path in kb_root.glob("*/transcript.txt"))
    return {
        "case_count": len(cases),
        "answerable_cases": len(recalls),
        "no_answer_cases": no_answer_cases,
        "recall_at_k": round(sum(recalls) / max(len(recalls), 1), 4),
        "mrr": round(sum(reciprocal_ranks) / max(len(reciprocal_ranks), 1), 4),
        "ndcg_at_k": round(sum(ndcgs) / max(len(ndcgs), 1), 4),
        "no_answer_accuracy": round(no_answer_hits / max(no_answer_cases, 1), 4),
        "unique_video_ratio": round(sum(diversity_scores) / max(len(diversity_scores), 1), 4),
        "citation_validity": round(citation_valid / max(citation_total, 1), 4),
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
        },
        "top_k_injected_chars": injected_chars,
        "full_transcript_bytes": transcript_bytes,
        "context_reduction": round(1 - injected_chars / max(transcript_bytes * len(cases), 1), 4),
        "cases": records,
    }


def benchmark_10k() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        index_path = Path(tmp) / "benchmark.sqlite3"
        db = _connect(index_path)
        now = "2026-01-01T00:00:00+00:00"
        terms = " ".join(tokenize("Python 环境管理 虚拟环境 依赖配置 Agent 知识检索"))
        for video in range(100):
            bvid = f"BV1BENCH{video:04d}"
            db.execute(
                """INSERT INTO videos
                   (bvid, source_url, title, author, video_type, duration, published_at,
                    fingerprint, content_hash, simhash, duplicate_of, near_duplicates,
                    chunk_count, indexed_at)
                   VALUES (?, ?, ?, '', 'knowledge', 100, '', ?, ?, 0, '', '[]', 100, ?)""",
                (bvid, f"https://www.bilibili.com/video/{bvid}/", f"基准视频 {video}", bvid, bvid, now),
            )
            rows = []
            for chunk in range(100):
                chunk_id = f"{bvid}-p1-{chunk:04d}"
                text = f"Python 环境管理与知识检索基准片段 {video} {chunk}"
                rows.append((
                    chunk_id, bvid, 1, "00:00", "00:05", 0, 5,
                    f"{bvid}#P1@00:00-00:05", text, chunk_id, terms,
                ))
            db.executemany(
                """INSERT INTO chunks
                   (chunk_id, bvid, part, start_time, end_time, start_seconds,
                    end_seconds, citation, text, content_hash, terms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        db.commit()
        db.close()
        latencies = []
        for _ in range(20):
            started = time.perf_counter()
            search_knowledge(
                "Python 环境管理 知识检索",
                top_k=6,
                index_path=index_path,
                _skip_sync=True,
            )
            latencies.append((time.perf_counter() - started) * 1000)
        return {
            "chunks": 10_000,
            "runs": len(latencies),
            "p50_ms": round(_percentile(latencies, 0.50), 3),
            "p95_ms": round(_percentile(latencies, 0.95), 3),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate personal video RAG retrieval")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--workspace", action="store_true", help="evaluate the current ignored workspace cache")
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    cases = _load_cases()
    if args.workspace:
        if args.reindex:
            rebuild_index()
        report = evaluate(
            kb_root=KB_ROOT,
            index_path=INDEX_PATH,
            cases=WORKSPACE_CASES,
            top_k=args.top_k,
        )
        report["dataset"] = "workspace"
    else:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge_base"
            index = Path(tmp) / "index.sqlite3"
            _build_fixture(root)
            rebuild_index(kb_root=root, index_path=index)
            report = evaluate(kb_root=root, index_path=index, cases=cases, top_k=args.top_k)
        report["dataset"] = "fixture"
    report["benchmark_10k"] = benchmark_10k()

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    passed = (
        report["recall_at_k"] >= 0.90
        and report["mrr"] >= 0.75
        and report["ndcg_at_k"] >= 0.80
        and report["no_answer_accuracy"] >= 0.90
        and report["benchmark_10k"]["p95_ms"] < 500
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
