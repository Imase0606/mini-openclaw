"""Redacted JSONL tracing, replay and token/cost reporting."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REDACTIONS = (
    re.compile(r"\b(?:sk|xai)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)(api[_ -]?key|token|password|secret)(\s*[:=]\s*)(\S+)"),
    re.compile(r"-----BEGIN .*?PRIVATE KEY-----.*?-----END .*?PRIVATE KEY-----", re.I | re.S),
)


def redact_text(value: Any, max_chars: int = 500) -> str:
    text = str(value if value is not None else "")
    for pattern in REDACTIONS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1\2[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text[:max_chars]


def _trace_safe(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in {"content", "data", "image", "image_url"}:
                out[key] = redact_text(item, 200)
            else:
                out[key] = _trace_safe(item)
        return out
    if isinstance(value, list):
        return [_trace_safe(item) for item in value[:30]]
    if isinstance(value, str):
        return redact_text(value, 500)
    return value


class Tracer:
    def __init__(self, path: str | Path | None = None) -> None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        self.path = Path(path or f".mini-openclaw/traces/{run_id}.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.spans: list[dict[str, Any]] = []
        self._last_request = ""

    def call(
        self,
        kind: str,
        name: str,
        fn: Callable[[], Any],
        *,
        input_data: Any = None,
        meta: dict[str, Any] | None = None,
    ) -> Any:
        started = time.perf_counter()
        ok = True
        output: Any = None
        error = ""
        try:
            output = fn()
            return output
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            usage = output.get("usage", {}) if isinstance(output, dict) else {}
            self.record(
                kind,
                name,
                ok=ok,
                ms=round((time.perf_counter() - started) * 1000),
                input_data=input_data,
                output=output if ok else error,
                usage=usage,
                meta=meta,
            )

    def record(
        self,
        kind: str,
        name: str,
        *,
        ok: bool = True,
        ms: int = 0,
        input_data: Any = None,
        output: Any = None,
        usage: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "seq": len(self.spans) + 1,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            "name": name,
            "ok": bool(ok),
            "ms": int(ms),
            "input": _trace_safe(input_data),
            "output": redact_text(output),
            "usage": {
                "prompt_tokens": int((usage or {}).get("prompt_tokens") or 0),
                "completion_tokens": int((usage or {}).get("completion_tokens") or 0),
                "total_tokens": int((usage or {}).get("total_tokens") or 0),
            },
            **(meta or {}),
        }
        self.spans.append(event)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def observe_request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
        safe_messages = []
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, list):
                content = [
                    {"type": block.get("type"), "text": block.get("text", "")}
                    if isinstance(block, dict) and block.get("type") == "text"
                    else {"type": "image", "data": "[IMAGE]"}
                    for block in content
                ]
            safe_messages.append({"role": message.get("role"), "content": content})
        current = json.dumps(
            {"messages": safe_messages, "tools": tools},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        common = 0
        for left, right in zip(self._last_request, current):
            if left != right:
                break
            common += 1
        self._last_request = current
        return common

    def summary(self) -> dict[str, Any]:
        llm = [span for span in self.spans if span["kind"] == "llm"]
        prompt_tokens = sum(span["usage"]["prompt_tokens"] for span in llm)
        completion_tokens = sum(span["usage"]["completion_tokens"] for span in llm)
        input_price = float(os.environ.get("MODEL_INPUT_USD_PER_1M", "0") or 0)
        output_price = float(os.environ.get("MODEL_OUTPUT_USD_PER_1M", "0") or 0)
        estimated_cost = prompt_tokens / 1_000_000 * input_price + completion_tokens / 1_000_000 * output_price
        priciest = max(llm, key=lambda span: span["usage"]["total_tokens"], default=None)
        return {
            "spans": len(self.spans),
            "llm_spans": len(llm),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated_cost_usd": round(estimated_cost, 6),
            "pricing_configured": bool(input_price or output_price),
            "priciest_span": priciest["seq"] if priciest else None,
            "priciest_tokens": priciest["usage"]["total_tokens"] if priciest else 0,
        }

    def log_step(
        self,
        step: int,
        tool_calls: list,
        prompt_tokens: int,
        completion_tokens: int,
        note: str = "",
    ) -> None:
        """Compatibility adapter for the original Day3 eval tracer API."""
        self.record(
            "legacy",
            f"step-{step}",
            input_data={"tool_calls": tool_calls},
            output=note,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        )


def load_spans(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def replay(path: str | Path) -> str:
    lines = []
    for span in load_spans(path):
        tokens = span.get("usage", {}).get("total_tokens", 0)
        prefix = f" prefix={span.get('prefix_chars')}" if span.get("prefix_chars") is not None else ""
        flag = "" if span.get("ok") else " FAIL"
        lines.append(
            f"#{span['seq']:02d} {span['kind']:<7} {span['name']:<20} "
            f"{span['ms']:>6}ms {tokens:>6}tok{prefix}{flag} -> {span.get('output', '')[:80]}"
        )
    return "\n".join(lines)


def cost_report(path: str | Path) -> dict[str, Any]:
    tracer = object.__new__(Tracer)
    tracer.spans = load_spans(path)
    return tracer.summary()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a mini-OpenClaw trace")
    parser.add_argument("path")
    args = parser.parse_args()
    print(replay(args.path))
    print(json.dumps(cost_report(args.path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
