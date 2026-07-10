"""Bilibili video extraction tools for knowledge-base generation."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import Tool


KB_ROOT = Path("knowledge_base")
BVID_RE = re.compile(r"(BV[0-9A-Za-z]+)")


def _extract_bvid(url: str) -> str:
    match = BVID_RE.search(url or "")
    if not match:
        raise ValueError("仅支持包含 BV 号的 B站公开视频链接")
    return match.group(1)


def _safe_name(text: str, max_len: int = 48) -> str:
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("._")
    return (text or "untitled")[:max_len]


def _job_dir(bvid: str) -> Path:
    path = KB_ROOT / bvid
    path.mkdir(parents=True, exist_ok=True)
    return path


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _to_simplified(text: str) -> tuple[str, str]:
    """Best-effort Traditional Chinese to Simplified Chinese conversion."""
    if not text:
        return "", ""
    try:
        from opencc import OpenCC
    except ImportError:
        return text, "未安装 opencc-python-reimplemented，未执行繁简转换。"
    try:
        return OpenCC("t2s").convert(text), ""
    except Exception as exc:  # noqa: BLE001 - keep the tool usable if OpenCC fails.
        return text, f"OpenCC 繁简转换失败：{type(exc).__name__}: {exc}"


def _normalize_record_text(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    warnings: list[str] = []
    normalized: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        text, warning = _to_simplified(str(item.get("text") or ""))
        item["text"] = text
        if warning and warning not in warnings:
            warnings.append(warning)
        normalized.append(item)
    return normalized, "；".join(warnings)


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _run_yt_dlp(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    candidates = [["yt-dlp"], [sys.executable, "-m", "yt_dlp"]]
    errors: list[str] = []
    for prefix in candidates:
        try:
            result = _run(prefix + args, timeout=timeout)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            continue
        if result.returncode == 0:
            return result
        errors.append(result.stderr.strip() or result.stdout.strip())
        if prefix[0] == sys.executable and "No module named yt_dlp" in result.stderr:
            break
    raise RuntimeError(
        "yt-dlp 不可用或执行失败。请先安装：pip install yt-dlp；详情："
        + " | ".join(e for e in errors if e)
    )


def _format_seconds(value: float | int | None) -> str:
    if value is None:
        return ""
    value = int(value)
    h, rem = divmod(value, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _metadata_from_bili_api(url: str) -> dict[str, Any]:
    import httpx

    bvid = _extract_bvid(url)
    headers = {
        "User-Agent": "Mozilla/5.0 mini-openclaw",
        "Referer": "https://www.bilibili.com/",
    }
    resp = httpx.get(
        "https://api.bilibili.com/x/web-interface/view",
        params={"bvid": bvid},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message") or f"B站 API 返回 code={payload.get('code')}")
    data = payload["data"]
    owner = data.get("owner") or {}
    stat = data.get("stat") or {}
    return {
        "platform": "bilibili",
        "source_url": url,
        "bvid": data.get("bvid") or bvid,
        "aid": data.get("aid"),
        "cid": data.get("cid"),
        "title": data.get("title") or bvid,
        "description": data.get("desc") or "",
        "author": owner.get("name") or "",
        "author_mid": owner.get("mid"),
        "duration": data.get("duration"),
        "duration_text": _format_seconds(data.get("duration")),
        "published_at": datetime.fromtimestamp(data["pubdate"]).isoformat()
        if data.get("pubdate") else "",
        "pages": [
            {
                "cid": p.get("cid"),
                "page": p.get("page"),
                "part": p.get("part"),
                "duration": p.get("duration"),
                "duration_text": _format_seconds(p.get("duration")),
            }
            for p in data.get("pages", [])
        ],
        "stats": {
            "view": stat.get("view"),
            "like": stat.get("like"),
            "coin": stat.get("coin"),
            "favorite": stat.get("favorite"),
            "reply": stat.get("reply"),
            "danmaku": stat.get("danmaku"),
            "share": stat.get("share"),
        },
        "cover": data.get("pic") or "",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _probe(url: str = "") -> str:
    if not url:
        return "[错误] video_probe 缺少必需参数 url"
    try:
        metadata = _metadata_from_bili_api(url)
    except Exception as exc:
        return f"[失败] 无法获取 B站公开视频元数据：{type(exc).__name__}: {exc}"

    job = _job_dir(metadata["bvid"])
    metadata_path = job / "metadata.json"
    metadata_path.write_text(_json(metadata), encoding="utf-8")
    brief = {
        "ok": True,
        "message": "已获取 B站公开 API 元数据",
        "metadata_path": str(metadata_path),
        "metadata": metadata,
    }
    return _json(brief)


def _strip_vtt(path: Path) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_time = ""
    last_text = ""
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "Kind:", "Language:")):
            continue
        if "-->" in line:
            current_time = line.split()[0].replace(".", ",")
            continue
        if re.fullmatch(r"\d+", line):
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if not text or text == last_text:
            continue
        segments.append({"start": current_time, "end": "", "text": text})
        last_text = text
    return segments


def _write_segments(path: Path, segments: list[dict[str, Any]], source: str) -> str:
    segments, warning = _normalize_record_text(segments)
    lines = [f"# transcript_source: {source}"]
    if warning:
        lines.append(f"# normalize_warning: {warning}")
    for seg in segments:
        start = seg.get("start") or ""
        end = seg.get("end") or ""
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if start or end:
            lines.append(f"[{start}-{end}] {text}")
        else:
            lines.append(text)
    content = "\n".join(lines).strip() + "\n"
    path.write_text(content, encoding="utf-8")
    return content


def _download_subtitles(url: str, job: Path, timeout: int) -> list[Path]:
    out = str(job / "subtitle.%(ext)s")
    _run_yt_dlp(
        [
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "zh-CN,zh-Hans,zh-Hant,zh,en",
            "--sub-format",
            "vtt/best",
            "-o",
            out,
            url,
        ],
        timeout=timeout,
    )
    return sorted(job.glob("subtitle*.vtt"))


def _transcribe_audio(url: str, job: Path, model_size: str, timeout: int) -> list[dict[str, Any]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("未安装 faster-whisper，无法在无字幕时执行 ASR：pip install faster-whisper") from exc

    with tempfile.TemporaryDirectory(prefix="mini_openclaw_audio_") as tmp:
        audio_out = str(Path(tmp) / "audio.%(ext)s")
        _run_yt_dlp(["-f", "ba/bestaudio", "-o", audio_out, url], timeout=timeout)
        audio_files = [p for p in Path(tmp).iterdir() if p.is_file()]
        if not audio_files:
            raise RuntimeError("yt-dlp 未下载到可用音频流")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(audio_files[0]), vad_filter=True)
        return [
            {
                "start": _format_seconds(seg.start),
                "end": _format_seconds(seg.end),
                "text": seg.text.strip(),
            }
            for seg in segments
            if seg.text.strip()
        ]


def _transcribe(
    url: str = "",
    model_size: str = "base",
    max_duration_seconds: int = 7200,
    timeout: int = 900,
) -> str:
    if not url:
        return "[错误] video_transcribe 缺少必需参数 url"
    max_duration_seconds = int(max_duration_seconds)
    timeout = int(timeout)
    try:
        metadata = _metadata_from_bili_api(url)
    except Exception as exc:
        return f"[失败] 无法确认 B站公开视频元数据：{type(exc).__name__}: {exc}"
    duration = metadata.get("duration") or 0
    if duration and duration > max_duration_seconds:
        return f"[失败] 视频时长 {duration} 秒超过限制 {max_duration_seconds} 秒，请调大 max_duration_seconds。"

    job = _job_dir(metadata["bvid"])
    (job / "metadata.json").write_text(_json(metadata), encoding="utf-8")
    transcript_path = job / "transcript.txt"

    subtitle_error = ""
    try:
        subtitle_files = _download_subtitles(url, job, timeout=min(timeout, 300))
        for subtitle in subtitle_files:
            segments = _strip_vtt(subtitle)
            if segments:
                content = _write_segments(transcript_path, segments, f"subtitle:{subtitle.name}")
                return _json({
                    "ok": True,
                    "source": "subtitle",
                    "bvid": metadata["bvid"],
                    "transcript_path": str(transcript_path),
                    "metadata_path": str(job / "metadata.json"),
                    "segments": len(segments),
                    "excerpt": content[:3000],
                })
    except Exception as exc:
        subtitle_error = f"{type(exc).__name__}: {exc}"

    try:
        segments = _transcribe_audio(url, job, model_size=model_size, timeout=timeout)
        content = _write_segments(transcript_path, segments, f"asr:faster-whisper:{model_size}")
        return _json({
            "ok": True,
            "source": "asr",
            "bvid": metadata["bvid"],
            "transcript_path": str(transcript_path),
            "metadata_path": str(job / "metadata.json"),
            "segments": len(segments),
            "subtitle_error": subtitle_error,
            "excerpt": content[:3000],
        })
    except Exception as exc:
        return _json({
            "ok": False,
            "bvid": metadata["bvid"],
            "metadata_path": str(job / "metadata.json"),
            "subtitle_error": subtitle_error,
            "asr_error": f"{type(exc).__name__}: {exc}",
            "message": "未能获取字幕或 ASR 转写；不要基于标题/搜索结果冒充视频内容。",
        })


def _frame_ocr(url: str = "", interval_seconds: int = 15, timeout: int = 900) -> str:
    if not url:
        return "[错误] video_frame_ocr 缺少必需参数 url"
    interval_seconds = int(interval_seconds)
    timeout = int(timeout)
    if not shutil.which("ffmpeg"):
        return "[失败] 未找到 ffmpeg，无法抽取关键帧。请先安装 ffmpeg 并加入 PATH。"
    try:
        import easyocr
    except ImportError:
        return "[失败] 未安装 easyocr，无法执行 OCR：pip install easyocr"
    try:
        metadata = _metadata_from_bili_api(url)
    except Exception as exc:
        return f"[失败] 无法确认 B站公开视频元数据：{type(exc).__name__}: {exc}"

    job = _job_dir(metadata["bvid"])
    frames_dir = job / "assets" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    notes_path = job / "visual_notes.jsonl"

    with tempfile.TemporaryDirectory(prefix="mini_openclaw_video_") as tmp:
        video_out = str(Path(tmp) / "video.%(ext)s")
        try:
            _run_yt_dlp(
                ["-f", "bv*[height<=720]+ba/b[height<=720]/best[height<=720]/best", "-o", video_out, url],
                timeout=timeout,
            )
        except Exception as exc:
            return f"[失败] 无法下载公开视频媒体流用于抽帧：{type(exc).__name__}: {exc}"
        video_files = [p for p in Path(tmp).iterdir() if p.is_file()]
        if not video_files:
            return "[失败] yt-dlp 未下载到可用于抽帧的视频文件"
        frame_pattern = str(frames_dir / "frame_%05d.jpg")
        ffmpeg = _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_files[0]),
                "-vf",
                f"fps=1/{max(1, interval_seconds)}",
                "-q:v",
                "3",
                frame_pattern,
            ],
            timeout=timeout,
        )
        if ffmpeg.returncode != 0:
            return f"[失败] ffmpeg 抽帧失败：{ffmpeg.stderr[-1000:]}"

    reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for idx, frame in enumerate(sorted(frames_dir.glob("frame_*.jpg"))):
        texts = [item[1].strip() for item in reader.readtext(str(frame)) if item[1].strip()]
        joined = " ".join(texts)
        if not joined or joined in seen:
            continue
        seen.add(joined)
        records.append({
            "time": _format_seconds(idx * interval_seconds),
            "frame": str(frame),
            "text": joined,
        })
    records, normalize_warning = _normalize_record_text(records)

    notes_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + ("\n" if records else ""),
        encoding="utf-8",
    )
    return _json({
        "ok": True,
        "bvid": metadata["bvid"],
        "visual_notes_path": str(notes_path),
        "frames_dir": str(frames_dir),
        "records": len(records),
        "normalize_warning": normalize_warning,
        "excerpt": records[:10],
    })


def _read_text_or_value(value: str = "", path: str = "") -> str:
    if path:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    return value or ""


def _load_metadata(value: str = "", path: str = "") -> dict[str, Any]:
    raw = _read_text_or_value(value, path)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _chunk_text(text: str, source_url: str, size: int = 1200) -> list[dict[str, Any]]:
    clean = "\n".join(line for line in text.splitlines() if line and not line.startswith("# transcript_source:"))
    chunks: list[dict[str, Any]] = []
    for idx in range(0, len(clean), size):
        body = clean[idx:idx + size].strip()
        if not body:
            continue
        chunks.append({
            "chunk_id": f"chunk-{len(chunks) + 1:03d}",
            "source_url": source_url,
            "start_time": "",
            "end_time": "",
            "text": body,
        })
    return chunks


def _kb_write(
    title: str = "",
    source_url: str = "",
    transcript: str = "",
    visual_notes: str = "",
    metadata: str = "",
    transcript_path: str = "",
    visual_notes_path: str = "",
    metadata_path: str = "",
    summary: str = "",
    content_digest: str = "",
    key_points: str = "",
    section_notes: str = "",
    action_suggestions: str = "",
) -> str:
    if not source_url:
        return "[错误] kb_write 缺少必需参数 source_url"
    meta = _load_metadata(metadata, metadata_path)
    bvid = meta.get("bvid") or (_extract_bvid(source_url) if BVID_RE.search(source_url) else hashlib.sha1(source_url.encode()).hexdigest()[:10])
    title = title or meta.get("title") or bvid
    title, title_warning = _to_simplified(title)
    job = _job_dir(bvid)

    transcript_text = _read_text_or_value(transcript, transcript_path)
    visual_text = _read_text_or_value(visual_notes, visual_notes_path)
    if not transcript_text and not visual_text:
        return "[错误] kb_write 需要 transcript/transcript_path 或 visual_notes/visual_notes_path，不能生成空知识库。"
    normalize_warnings: list[str] = []
    transcript_text, transcript_warning = _to_simplified(transcript_text)
    visual_text, visual_warning = _to_simplified(visual_text)
    for warning in (transcript_warning, visual_warning):
        if warning and warning not in normalize_warnings:
            normalize_warnings.append(warning)
    summary, summary_warning = _to_simplified(summary)
    content_digest, digest_warning = _to_simplified(content_digest)
    key_points, key_points_warning = _to_simplified(key_points)
    section_notes, notes_warning = _to_simplified(section_notes)
    action_suggestions, action_warning = _to_simplified(action_suggestions)
    for warning in (
        title_warning,
        summary_warning,
        digest_warning,
        key_points_warning,
        notes_warning,
        action_warning,
    ):
        if warning and warning not in normalize_warnings:
            normalize_warnings.append(warning)
    digest = content_digest or summary

    metadata_out = job / "metadata.json"
    if meta:
        if normalize_warnings:
            meta["normalize_warning"] = "；".join(normalize_warnings)
        metadata_out.write_text(_json(meta), encoding="utf-8")
    transcript_out = job / "transcript.txt"
    if transcript_text:
        transcript_out.write_text(transcript_text, encoding="utf-8")
    visual_out = job / "visual_notes.jsonl"
    if visual_text:
        visual_out.write_text(visual_text, encoding="utf-8")

    chunks = _chunk_text(transcript_text or visual_text, source_url)
    chunks_path = job / "chunks.jsonl"
    chunks_path.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + ("\n" if chunks else ""),
        encoding="utf-8",
    )

    md_path = job / "index.md"
    basis = []
    if transcript_text:
        first = transcript_text.splitlines()[0] if transcript_text.splitlines() else "转写文本"
        basis.append(first.replace("# transcript_source: ", ""))
    if visual_text:
        basis.append("OCR/关键帧视觉补充")
    files = [
        f"- Markdown：`{md_path.name}`",
        f"- 元数据：`{metadata_out.name}`",
        f"- RAG 切块：`{chunks_path.name}`（{len(chunks)} 条）",
    ]
    if transcript_text:
        files.append(f"- 转写文本：`{transcript_out.name}`")
    if visual_text:
        files.append(f"- 画面 OCR：`{visual_out.name}`")

    section_heading = "## 按时间/段落整理"
    has_timestamps = bool(re.search(r"^\[[0-9:,]+-", transcript_text, flags=re.MULTILINE))
    default_notes = (
        "请基于 `transcript.txt` 中的时间戳整理视频脉络。"
        if has_timestamps
        else "当前转写缺少可靠时间戳，请按主题段落整理视频脉络。"
    )
    visual_section = (
        f"\n## 画面补充信息\n{visual_text[:3000]}\n"
        if visual_text.strip()
        else ""
    )
    action_section = (
        f"\n## 行动建议/学习建议\n{action_suggestions.strip()}\n"
        if action_suggestions.strip()
        else ""
    )
    warning_section = (
        "\n- 文本归一化：" + "；".join(normalize_warnings)
        if normalize_warnings
        else ""
    )
    md = f"""# {title}

## 来源信息
- 来源链接：{source_url}
- 平台：B站（bilibili）
- 作者/UP主：{meta.get("author", "")}
- 发布时间：{meta.get("published_at", "")}
- 生成时间：{datetime.now().date().isoformat()}
- 内容依据：{", ".join(basis) if basis else "用户提供内容"}

## 来源与文件
{chr(10).join(files)}

## 内容提要
{digest or "待 agent 基于 transcript/visual_notes 写成 1-3 个自然段，通常 150-400 字；需要覆盖主题背景、主要观点、关键论证链路、结论价值和适用场景。"}

## 核心要点
{key_points or "- 待 agent 基于真实转写内容提炼。"}

{section_heading}
{section_notes or default_notes}
{visual_section}{action_section}
## 信息缺口与可信度说明
- 已确认：本文件基于本地保存的 transcript/visual_notes/metadata 生成。
- 缺失：若 transcript 或 visual_notes 为空，对应模态内容未成功提取。
- 推测：未由 transcript 或 OCR 直接支持的观点，需要在后续总结中标明。{warning_section}
"""
    md_path.write_text(md, encoding="utf-8")
    return _json({
        "ok": True,
        "markdown_path": str(md_path),
        "metadata_path": str(metadata_out),
        "transcript_path": str(transcript_out) if transcript_text else "",
        "visual_notes_path": str(visual_out) if visual_text else "",
        "chunks_path": str(chunks_path),
        "chunks": len(chunks),
        "normalize_warning": "；".join(normalize_warnings),
    })


video_probe_tool = Tool(
    "video_probe",
    "解析 B站 BV 链接并获取公开视频元数据，写入 knowledge_base/<BV>/metadata.json。",
    {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    _probe,
)

video_transcribe_tool = Tool(
    "video_transcribe",
    "提取 B站公开视频字幕；无字幕时用 yt-dlp 下载音频并用 faster-whisper 本地转写。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "model_size": {"type": "string", "default": "base"},
            "max_duration_seconds": {"type": "integer", "default": 7200},
            "timeout": {"type": "integer", "default": 900},
        },
        "required": ["url"],
    },
    _transcribe,
)

video_frame_ocr_tool = Tool(
    "video_frame_ocr",
    "用 yt-dlp/ffmpeg 抽取 B站公开视频关键帧，并用 easyocr 提取画面文字。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "interval_seconds": {"type": "integer", "default": 15},
            "timeout": {"type": "integer", "default": 900},
        },
        "required": ["url"],
    },
    _frame_ocr,
)

kb_write_tool = Tool(
    "kb_write",
    "将 transcript/visual_notes/metadata 写成 Markdown 知识库和 RAG-ready chunks.jsonl。",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "source_url": {"type": "string"},
            "transcript": {"type": "string"},
            "visual_notes": {"type": "string"},
            "metadata": {"type": "string"},
            "transcript_path": {"type": "string"},
            "visual_notes_path": {"type": "string"},
            "metadata_path": {"type": "string"},
            "summary": {"type": "string", "description": "兼容旧参数；建议改用 content_digest。"},
            "content_digest": {"type": "string", "description": "1-3 个自然段，通常 150-400 字的视频内容提要。"},
            "key_points": {"type": "string", "description": "面向人类学习笔记的核心要点。"},
            "section_notes": {"type": "string", "description": "按时间或主题段落整理的视频脉络。"},
            "action_suggestions": {"type": "string", "description": "仅在视频包含教程/方法论/可执行建议时填写。"},
        },
        "required": ["source_url"],
    },
    _kb_write,
)
