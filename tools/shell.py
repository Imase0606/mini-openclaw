"""Controlled shell execution with bubblewrap and conservative fallback checks."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from .base import Tool


NETWORK_COMMANDS = {
    "curl", "wget", "nc", "netcat", "ncat", "ssh", "scp", "sftp", "telnet",
}
SYSTEM_COMMANDS = {
    "mkfs", "fdisk", "parted", "mount", "umount", "shutdown", "reboot", "poweroff",
}
FALLBACK_FORBIDDEN_PATHS = ("../", "/etc/", "/root/", "~/.ssh", "$home/.ssh")


def _command_name(tokens: list[str]) -> tuple[str, list[str]]:
    while tokens and tokens[0] in {"sudo", "command", "env"}:
        tokens = tokens[1:]
    if not tokens:
        return "", []
    return Path(tokens[0]).name.lower(), tokens[1:]


def dangerous_reason(command: str, *, fallback: bool = False) -> str:
    """Return a refusal reason, or an empty string when the command may proceed."""
    lowered = command.lower()
    if re.search(r":\s*\(\s*\)\s*\{.*:\s*\|\s*:\s*&", lowered, re.DOTALL):
        return "检测到 fork bomb"
    if re.search(r">\s*/dev/(?:sd|nvme|vd|xvd)", lowered):
        return "禁止写入块设备"
    if "dd if=" in lowered or re.search(r"\bdd\b.*\bof=/dev/", lowered):
        return "禁止使用 dd 复制磁盘或写入设备"
    if "invoke-webrequest" in lowered or re.search(r"(?:^|[;&|\s])iwr(?:[;&|\s]|$)", lowered):
        return "Shell 内禁止网络外传命令"
    network_pattern = "|".join(re.escape(name) for name in sorted(NETWORK_COMMANDS, key=len, reverse=True))
    if re.search(rf"(?<![\w-])(?:{network_pattern})(?![\w-])", lowered):
        return "Shell 内禁止网络命令或嵌套网络命令"

    for segment in re.split(r"(?:&&|\|\||[;|\n])", command):
        try:
            tokens = shlex.split(segment, posix=os.name != "nt")
        except ValueError:
            return "命令引号不完整，无法安全解析"
        name, args = _command_name(tokens)
        if not name:
            continue
        if name in NETWORK_COMMANDS:
            return f"Shell 内禁止网络命令：{name}"
        if name in SYSTEM_COMMANDS or name.startswith("mkfs."):
            return f"禁止系统级命令：{name}"
        if name == "rm":
            flags = "".join(arg.lstrip("-") for arg in args if arg.startswith("-"))
            if "r" in flags and "f" in flags:
                return "禁止递归强制删除"
        if name == "git" and args:
            if args[:2] == ["reset", "--hard"]:
                return "禁止 git reset --hard"
            if args[0] == "clean" and any("f" in arg.lstrip("-") for arg in args[1:]):
                return "禁止 git clean 强制删除"

    if fallback and any(path in lowered for path in FALLBACK_FORBIDDEN_PATHS):
        return "无 bubblewrap 时禁止访问工作区外敏感路径"
    return ""


def _sandbox_command(command: str, cwd: Path, bwrap: str) -> list[str]:
    return [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--ro-bind", "/", "/",
        "--bind", str(cwd), str(cwd),
        "--unshare-net",
        "--dev", "/dev",
        "--proc", "/proc",
        "--chdir", str(cwd),
        "/bin/bash", "-c", command,
    ]


def _bash(command: str = "", timeout: int = 30) -> str:
    if not command:
        return "[错误] bash 缺少必需参数 command"
    timeout = max(1, min(int(timeout), 120))
    cwd = Path.cwd().resolve()
    bwrap = shutil.which("bwrap") if os.name != "nt" else None
    reason = dangerous_reason(command, fallback=bwrap is None)
    if reason:
        return f"[沙箱] 拒绝执行：{reason}"

    if bwrap:
        cmd: list[str] | str = _sandbox_command(command, cwd, bwrap)
        shell = False
        prefix = ""
    elif os.name == "nt":
        cmd = command
        shell = True
        prefix = "[沙箱降级] 当前平台无 bubblewrap，已使用黑名单与路径检查。\n"
    else:
        cmd = ["/bin/bash", "-c", command]
        shell = False
        prefix = "[沙箱降级] 未找到 bubblewrap，已使用黑名单与路径检查。\n"

    try:
        process = subprocess.run(
            cmd,
            shell=shell,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {timeout}s 未结束"
    except OSError as exc:
        return f"[沙箱] 启动失败：{type(exc).__name__}: {exc}"

    output = process.stdout or ""
    if process.stderr:
        output += f"\n[stderr]\n{process.stderr}"
    if process.returncode != 0:
        output += f"\n[returncode={process.returncode}]"
    return prefix + (output.strip() or "[无输出]")


bash_tool = Tool(
    name="bash",
    description="在受控沙箱中执行命令；系统只读、工作区可写、网络隔离。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 120, "default": 30},
        },
        "required": ["command"],
    },
    run=_bash,
)
