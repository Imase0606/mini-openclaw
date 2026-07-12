"""State records used only by the Textual presentation layer."""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class PermissionRequest:
    request_id: str
    tool_name: str
    arguments: dict
    resolved: threading.Event
    approved: bool = False


@dataclass(frozen=True)
class ArtifactRecord:
    kind: str
    path: str


@dataclass
class TUISettings:
    planning_mode: str = "auto"
    video_type: str = "auto"
    permission_mode: str = "default"
    model_alias: str = "deepseek"


@dataclass(frozen=True)
class QueuedRequest:
    kind: str
    text: str
    image_paths: tuple[str, ...]
    settings: TUISettings
