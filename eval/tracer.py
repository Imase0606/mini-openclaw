"""Compatibility import for the canonical Day9 tracer implementation."""
from __future__ import annotations

from agent.tracer import Tracer, cost_report, replay


__all__ = ["Tracer", "replay", "cost_report"]


if __name__ == "__main__":
    from eval.metrics import SAMPLE_RECORDS

    record = SAMPLE_RECORDS[0]
    tracer = Tracer(".mini-openclaw/traces/eval-sample.jsonl")
    for index, step in enumerate(record["steps"], 1):
        tracer.log_step(
            index,
            step.get("tool_calls", []),
            step.get("prompt_tokens", 0),
            step.get("completion_tokens", 0),
            note=step.get("raw", "")[:80],
        )
    print(replay(tracer.path))
