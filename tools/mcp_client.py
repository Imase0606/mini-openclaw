"""最小 MCP 客户端（Day8）。

MCP（Model Context Protocol）让工具集从"写死在代码里"变成"可插拔的外部 server"。
本文件实现一个最小客户端：通过 stdio 跟 server 通信，做 JSON-RPC。

要实现的握手与调用：
  1. 启动 server 子进程（stdio transport）
  2. initialize 握手
  3. tools/list  —— 拉取 server 暴露的工具
  4. tools/call  —— 把某次调用转发给 server，拿回结果
然后在 agent/loop 里，把这些 MCP 工具**透明合并**进内置 ToolRegistry。

鲁棒性处理：
  - start 后进程立即退出 → 抛可读异常
  - readline 返回空 → 进程已退出 → 抛异常
  - 响应含 error 字段 → 抛 RuntimeError（被 loop 的 try/except 捕获为 observation）
"""
from __future__ import annotations
import json
import os
import queue
import shutil
import subprocess
import threading
from typing import Any

from tools.base import Tool, ToolRegistry


class MCPError(RuntimeError):
    """MCP 通信层异常（启动失败、进程退出、server 返回 error）。"""
    pass


class MCPClient:
    def __init__(
        self,
        command: list[str],
        name: str = "mcp",
        startup_timeout: float = 10.0,
        request_timeout: float = 30.0,
    ):
        self.command = command
        self.name = name
        self.proc: subprocess.Popen | None = None
        self._id = 0
        self._started = False
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout

    def _check_proc(self) -> None:
        """检查子进程是否存活，若已退出则抛异常。"""
        if self.proc is None:
            raise MCPError(f"MCP {self.name} 未启动")
        ret = self.proc.poll()
        if ret is not None:
            raise MCPError(f"MCP {self.name} 已退出，returncode={ret}")

    def start(self) -> None:
        # 强制子进程使用 UTF-8 编码，避免 Windows GBK 导致中文乱码
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["LANG"] = "en_US.UTF-8"

        # 解析命令路径：Windows 上 npx 是 npx.cmd，默认 Popen 找不到
        cmd0 = shutil.which(self.command[0])
        if os.name != "nt" and self.command[0] == "npx" and cmd0:
            if cmd0.startswith("/mnt/"):
                raise MCPError(
                    "WSL 检测到 Windows npx；请安装 Linux Node.js/npm，或调整 PATH 让 Linux npx 优先"
                )
            # MSYS2/MINGW64 (Git Bash) 下 shutil.which 返回 /d/... 格式路径，
            # Windows Popen 不认识这种路径。改用 npx.cmd 让 Windows PATH 解析。
            if cmd0.startswith("/") and not cmd0.startswith("/mnt/"):
                cmd0 = "npx.cmd"
        resolved_cmd = [cmd0] + self.command[1:] if cmd0 else self.command

        try:
            self.proc = subprocess.Popen(
                resolved_cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=child_env,
            )
        except FileNotFoundError as e:
            raise MCPError(f"MCP {self.name} 启动失败：{e}（命令 {self.command[0]} 未找到）") from e
        except Exception as e:
            raise MCPError(f"MCP {self.name} 启动失败：{e}") from e

        # 确认进程存活
        self._check_proc()

        try:
            self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mini-openclaw", "version": "0.1"},
            }, timeout=self.startup_timeout)
            self._notify("notifications/initialized")
            self._started = True
        except Exception:
            self.close()
            raise

    def _read_line(self, timeout: float | None = None) -> str:
        """从子进程 stdout 读一行。返回空串表示进程已死。"""
        self._check_proc()
        assert self.proc is not None and self.proc.stdout is not None
        stdout = self.proc.stdout
        result: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

        def read_stdout() -> None:
            try:
                result.put(stdout.readline())
            except BaseException as exc:  # noqa: BLE001 - forward reader failures.
                result.put(exc)

        threading.Thread(target=read_stdout, daemon=True).start()
        try:
            value = result.get(timeout=timeout or self.request_timeout)
        except queue.Empty as exc:
            raise MCPError(f"MCP {self.name} 响应超时（{timeout or self.request_timeout:g}s）") from exc
        if isinstance(value, BaseException):
            raise MCPError(f"MCP {self.name} 读取响应失败：{value}") from value
        line = value
        if line == "":
            ret = self.proc.poll()
            raise MCPError(
                f"MCP {self.name} 连接断开（stdout 关闭，returncode={ret}）"
            )
        return line.strip()

    def _rpc(
        self,
        method: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> Any:
        self._check_proc()
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self._read_line(timeout=timeout)
        try:
            resp = json.loads(line)
        except json.JSONDecodeError as e:
            raise MCPError(f"MCP {self.name} 返回非法 JSON：{e}，原始数据：{line[:200]}") from e
        if "error" in resp and resp["error"] is not None:
            err = resp["error"]
            raise MCPError(f"MCP {self.name} 返回错误：code={err.get('code')}, message={err.get('message')}")
        return resp["result"]

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._check_proc()
        req = {"jsonrpc": "2.0", "method": method, "params": params or {}}  # 无 id
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts)

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        self._started = False
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def register_mcp_tools(registry: ToolRegistry, client: MCPClient) -> None:
    """把一个 MCP server 的工具包装成内置 Tool 并注册，实现透明合并。"""
    for spec in client.list_tools():
        name = spec["name"]
        registry.register(Tool(
            name=f"mcp__{name}",            # 命名空间避免和内置工具撞名
            description=spec.get("description", ""),
            parameters=spec.get("inputSchema", {"type": "object", "properties": {}}),
            run=lambda _n=name, **kw: client.call_tool(_n, kw),
        ))
