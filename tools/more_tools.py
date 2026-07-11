"""完整工具集：edit / grep / glob（Day6，→ v1）+ web_fetch / task_list（Day7）。

每个工具上午讲设计权衡，下午实现。这里只给签名与 TODO，便于你拆到独立文件。
建议最终拆成 edit.py / search.py / web.py / todo.py，再在 base.build_default_registry 注册。
"""
from __future__ import annotations
import os
from .base import Tool
from .path_security import is_safe_workspace_file, workspace_path


# --- edit：三种策略权衡（整文件重写 / unified diff / search-replace）---
def _edit(path: str, old: str = "", new: str = "") -> str:
    safe_path = workspace_path(path)
    with safe_path.open("r", encoding="utf-8") as f:
        text = f.read()
    count = text.count(old)
    if count == 0:
        return f"[失败] 未找到待替换文本，请照抄文件原文（含缩进）。path={path}"
    if count > 1:
        return f"[失败] old 在文件中出现 {count} 次，不唯一；请扩大 old 片段使其唯一。"
    with safe_path.open("w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    return f"已在 {path} 完成 1 处替换。"


# --- grep：基于 ripgrep ---
import subprocess

def _grep(pattern: str = "", path: str = ".", max_lines: int = 100) -> str:
    if not pattern:
        return "[错误] grep 缺少必需参数 pattern"
    try:
        safe_path = workspace_path(path)
    except PermissionError as exc:
        return f"[拒绝] {exc}"
    try:
        p = subprocess.run(
            [
                "rg", "--line-number", "--no-heading",
                "--glob", "!.git/**", "--glob", "!.ssh/**", "--glob", "!.env*",
                "--glob", "!*.key", "--glob", "!*.pem", pattern, str(safe_path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except FileNotFoundError:
        return "[失败] 未找到 rg，请先安装 ripgrep。"
    if p.returncode not in (0, 1):  # 1 = 无匹配，属正常
        return f"[grep 出错] {p.stderr.strip()}"
    lines = p.stdout.splitlines()
    if not lines:
        return f"[无匹配] pattern={pattern}"
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n... [共 {len(lines)} 行，已截断前 {max_lines} 行]"
    return "\n".join(lines)

# --- glob：按文件名模式找文件 ---
from pathlib import Path

def _glob(pattern: str, max_items: int = 100) -> str:
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        return f"[拒绝] glob 模式不能越过工作区：{pattern}"
    root = Path.cwd().resolve()
    paths = [
        str(p.relative_to(root))
        for p in root.rglob(pattern)
        if is_safe_workspace_file(p)
    ]
    if not paths:
        return f"[无匹配] pattern={pattern}"
    if len(paths) > max_items:
        return "\n".join(paths[:max_items]) + f"\n... [共 {len(paths)} 个，已截断前 {max_items} 个]"
    return "\n".join(paths)


# --- web_fetch：URL -> markdown，控 token 预算 ---
from urllib.parse import urljoin, urlparse

DEFAULT_WEB_FETCH_HOSTS = {
    "10.130.130.9",
    "example.com",
    "api.deepseek.com",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "www.bilibili.com",
    "api.bilibili.com",
    "b23.tv",
}


def web_fetch_allow_hosts() -> set[str]:
    configured = {
        host.strip().lower().rstrip(".")
        for host in os.environ.get("WEB_FETCH_ALLOW_HOSTS", "").split(",")
        if host.strip()
    }
    return DEFAULT_WEB_FETCH_HOSTS | configured


def _validate_web_url(url: str, allow_hosts: set[str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("web_fetch 只允许 http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("URL 不允许包含用户名或密码")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host not in allow_hosts:
        raise ValueError(f"域名不在 web_fetch 白名单：{host or '[empty]'}")
    return host


def _web_fetch(url: str, max_tokens: int = 2000) -> str:
    import httpx
    from markdownify import markdownify as md
    from agent.context import truncate_observation

    allow_hosts = web_fetch_allow_hosts()
    current = url
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            for _hop in range(6):
                _validate_web_url(current, allow_hosts)
                resp = client.get(current, headers={"User-Agent": "mini-openclaw/0.1"})
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("location")
                    if not location:
                        return "[出站策略] 拒绝：重定向响应缺少 Location"
                    current = urljoin(current, location)
                    continue
                resp.raise_for_status()
                text = md(resp.text)
                max_tokens = max(1, min(int(max_tokens), 10_000))
                return truncate_observation(text, max_chars=max_tokens * 4)
    except ValueError as exc:
        return f"[出站策略] 拒绝：{exc}"
    return "[出站策略] 拒绝：重定向次数超过 5 次"


# --- task_list（TodoWrite）：自维护待办，提升长任务成功率 ---
def _task_list(action: str, items: list | None = None) -> str:
    # TODO[Day7] 维护一个结构化待办（add/update/complete），作为模型的 scratchpad
    raise NotImplementedError("Day7：实现 task_list")


edit_tool = Tool("edit", "编辑文件：把 old 文本替换为 new。",
                 {"type": "object", "properties": {"path": {"type": "string"},
                  "old": {"type": "string"}, "new": {"type": "string"}},
                  "required": ["path", "old", "new"]}, _edit)
grep_tool = Tool("grep", "在文件中搜索匹配 pattern 的行（基于 ripgrep）。",
                 {"type": "object", "properties": {"pattern": {"type": "string"},
                  "path": {"type": "string"}}, "required": ["pattern"]}, _grep)
glob_tool = Tool("glob", "按通配模式查找文件路径。",
                 {"type": "object", "properties": {"pattern": {"type": "string"}},
                  "required": ["pattern"]}, _glob)
web_fetch_tool = Tool("web_fetch", "抓取出站白名单 URL 并转为 markdown（逐跳校验重定向）。",
                      {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}, _web_fetch)
task_list_tool = Tool("task_list", "维护任务待办清单（add/update/complete）。",
                      {"type": "object", "properties": {"action": {"type": "string"},
                       "items": {"type": "array"}}, "required": ["action"]}, _task_list)
