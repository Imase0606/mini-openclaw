"""Code-enforced tool policy for least-privilege agent tasks."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.base import ToolRegistry
from tools.path_security import workspace_path
from tools.external import wrap_external
from agent import permissions


BVID_RE = re.compile(r"BV[0-9A-Za-z]+")
VIDEO_TOOLS = {
    "read", "video_probe", "video_transcribe", "video_frame_ocr", "kb_write",
    "remember", "forget_memory", "recall_memory", "todo_write", "update_todo", "insert_todo",
}
UNTRUSTED_CONTENT_TOOLS = {"read", "web_fetch", "video_probe", "video_transcribe", "video_frame_ocr"}


class ToolPolicy:
    def __init__(
        self,
        *,
        video_mode: bool = False,
        task: str = "",
        workdir: Path | None = None,
    ) -> None:
        self.video_mode = video_mode
        self.allowed_bvids = set(BVID_RE.findall(task))
        self.workdir = (workdir or Path.cwd()).resolve()

    def schemas(self, registry: ToolRegistry) -> list[dict[str, Any]]:
        schemas = registry.schemas()
        selected = schemas if not self.video_mode else [
            schema for schema in schemas
            if schema.get("function", {}).get("name") in VIDEO_TOOLS
        ]
        return sorted(selected, key=lambda schema: schema.get("function", {}).get("name", ""))

    def authorize(self, name: str, arguments: dict[str, Any]) -> tuple[permissions.Verdict, str]:
        if not self.video_mode:
            verdict = permissions.check(name, arguments, self.workdir)
            reason = {
                "allow": "只读工具自动放行",
                "confirm": "写入、执行、外传或未知工具需要用户确认",
                "deny": "路径越过工作区、命中敏感文件或参数无效",
            }[verdict]
            return verdict, reason
        if name not in VIDEO_TOOLS:
            return "deny", f"视频总结任务禁止调用高权限工具：{name}"
        if name in {"remember", "forget_memory"}:
            return "confirm", "长期记忆写入或遗忘需要用户确认"
        if name in {"video_probe", "video_transcribe", "video_frame_ocr", "kb_write"}:
            url = str(arguments.get("source_url") or arguments.get("url") or "")
            bvids = set(BVID_RE.findall(url))
            if self.allowed_bvids and (not bvids or not bvids.issubset(self.allowed_bvids)):
                return "deny", "视频工具 URL 的 BV 号与当前任务/探测结果不一致"
        if name == "read":
            return self._authorize_video_read(str(arguments.get("path") or ""))
        return "allow", "视频任务白名单工具"

    def _authorize_video_read(self, path: str) -> tuple[permissions.Verdict, str]:
        if not path:
            return "deny", "视频总结任务的 read 缺少 path"
        if Path(path).is_absolute() or ".." in Path(path).parts:
            return "deny", "视频总结任务禁止绝对路径和上级目录跳转"
        try:
            resolved = workspace_path(path, root=self.workdir)
            relative = resolved.relative_to(self.workdir)
        except (OSError, PermissionError, ValueError) as exc:
            return "deny", str(exc)
        parts = relative.parts
        if len(parts) < 3 or parts[0] != "knowledge_base" or parts[1] not in self.allowed_bvids:
            return "deny", "视频总结任务只能读取当前视频的 knowledge_base/<BV>/ 文件"
        return "allow", "当前视频知识库文件"

    def observe(self, name: str, observation: str) -> None:
        if not self.video_mode or name not in {"video_probe", "video_transcribe", "video_frame_ocr", "kb_write"}:
            return
        try:
            payload = json.loads(observation)
        except (TypeError, json.JSONDecodeError):
            return
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        bvid = payload.get("bvid") or metadata.get("bvid")
        if isinstance(bvid, str) and BVID_RE.fullmatch(bvid):
            self.allowed_bvids.add(bvid)

    @staticmethod
    def wrap_observation(
        name: str,
        observation: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        if name not in UNTRUSTED_CONTENT_TOOLS:
            return observation
        args = arguments or {}
        source = str(args.get("path") or args.get("url") or name)
        return wrap_external(observation, source)
