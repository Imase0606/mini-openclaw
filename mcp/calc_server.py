"""一个最小 MCP server（自写 calc），暴露 add(a,b) 和 multiply(a,b)。

作为 Day8 练习：用多工具情形练 tools/list 和 tools/call。
若 npx 不可用，可退而用此 server 替代 filesystem server。
"""
from __future__ import annotations
import json
import sys

TOOLS = [
    {
        "name": "add",
        "description": "返回两个数的和。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个加数"},
                "b": {"type": "number", "description": "第二个加数"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "multiply",
        "description": "返回两个数的积。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个乘数"},
                "b": {"type": "number", "description": "第二个乘数"},
            },
            "required": ["a", "b"],
        },
    },
]


def handle(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"protocolVersion": "2024-11-05",
                           "serverInfo": {"name": "calc", "version": "0.1"},
                           "capabilities": {"tools": {}}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {})
        try:
            if name == "add":
                result = args["a"] + args["b"]
            elif name == "multiply":
                result = args["a"] * args["b"]
            else:
                return {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32601, "message": f"unknown tool: {name}"}}
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": str(result)}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32000, "message": str(e)}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = handle(json.loads(line))
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
