"""Helpers for building image content blocks for multimodal model requests."""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps


MAX_IMAGE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_EDGE = 1568


def image_block(path: str | Path, max_edge: int = DEFAULT_MAX_EDGE) -> dict[str, Any]:
    """Load, resize, and encode an image as an internal base64 content block."""
    image_path = Path(path)
    if not image_path.is_file():
        raise FileNotFoundError(f"图片不存在：{image_path}")
    if image_path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 限制：{image_path}")
    if max_edge <= 0:
        raise ValueError("max_edge 必须大于 0")

    try:
        with Image.open(image_path) as source:
            source.load()
            source_format = (source.format or "PNG").upper()
            image = ImageOps.exif_transpose(source)
            if max(image.size) > max_edge:
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            if source_format in {"JPEG", "JPG"}:
                image.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
                media_type = "image/jpeg"
            else:
                if image.mode not in {"RGB", "RGBA", "L", "LA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                image.save(output, format="PNG", optimize=True)
                media_type = "image/png"
    except Image.UnidentifiedImageError as exc:
        raise ValueError(f"无法识别图片格式：{image_path}") from exc

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(output.getvalue()).decode("ascii"),
        },
    }


def multimodal_user_content(text: str, image_paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    """Build one user content list containing text followed by image blocks."""
    content: list[dict[str, Any]] = [
        {"type": "text", "text": text.strip() or "请描述图片中的内容。"}
    ]
    content.extend(image_block(path) for path in image_paths)
    return content
