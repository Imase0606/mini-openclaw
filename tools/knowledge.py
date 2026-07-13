"""Persistent, lightweight retrieval over accumulated video knowledge bases."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sqlite3
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .base import Tool
from .path_security import workspace_path


KB_ROOT = Path("knowledge_base")
INDEX_PATH = Path(".mini-openclaw/video_knowledge.sqlite3")
TRASH_ROOT = KB_ROOT / ".trash"
SCHEMA_VERSION = 2
TIME_LINE_RE = re.compile(r"^\[([^\]-]+)-([^\]]*)\]\s*(.+)$")
PART_RE = re.compile(r"^##\s*P(\d+)(?::|\s|$)", re.I)
VIDEO_TYPE_RE = re.compile(r"视频类型：.*?`(tutorial|knowledge|narrative|commentary|general)`")
ASCII_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]*", re.I)
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
BVID_RE = re.compile(r"BV[0-9A-Za-z]+")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def tokenize(text: str) -> list[str]:
    """Tokenize Chinese without an external segmenter and preserve ASCII terms."""
    lowered = str(text or "").lower()
    tokens = [token for token in ASCII_TOKEN_RE.findall(lowered) if len(token) >= 2]
    for run in CHINESE_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _normalized_content(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip().lower()
        if not line or line.startswith("# transcript_source:") or line.startswith("# normalize_warning:"):
            continue
        line = TIME_LINE_RE.sub(lambda match: match.group(3), line)
        lines.append(" ".join(line.split()))
    return "\n".join(lines)


def _content_hash(text: str) -> str:
    return hashlib.sha256(_normalized_content(text).encode("utf-8")).hexdigest()


def _simhash(text: str) -> int:
    frequencies = Counter(tokenize(_normalized_content(text)))
    vector = [0] * 64
    for token, weight in frequencies.items():
        value = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")
        for bit in range(64):
            vector[bit] += weight if value & (1 << bit) else -weight
    result = 0
    for bit, weight in enumerate(vector):
        if weight >= 0:
            result |= 1 << bit
    return result if result < (1 << 63) else result - (1 << 64)


def _simhash_similarity(left: int, right: int) -> float:
    mask = (1 << 64) - 1
    return 1.0 - (((left & mask) ^ (right & mask)).bit_count() / 64)


def _seconds(value: str) -> int | None:
    parts = value.strip().replace(",", ".").split(":")
    if not parts or any(not re.fullmatch(r"\d+(?:\.\d+)?", part) for part in parts):
        return None
    numbers = [float(part) for part in parts]
    if len(numbers) == 2:
        return int(numbers[0] * 60 + numbers[1])
    if len(numbers) == 3:
        return int(numbers[0] * 3600 + numbers[1] * 60 + numbers[2])
    return None


def _citation(bvid: str, part: int, start: str, end: str) -> str:
    if not start:
        return f"{bvid}#P{part}@time_unavailable"
    return f"{bvid}#P{part}@{start}-{end or start}"


def build_transcript_chunks(
    transcript: str,
    *,
    bvid: str,
    source_url: str,
    title: str = "",
    author: str = "",
    video_type: str = "general",
    target_chars: int = 800,
    max_chars: int = 1200,
) -> list[dict[str, Any]]:
    """Group complete transcript segments without crossing video-part boundaries."""
    target_chars = max(200, min(int(target_chars), max_chars))
    max_chars = max(target_chars, int(max_chars))
    chunks: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    part = 1
    sequence_by_part: dict[int, int] = {}

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        sequence_by_part[part] = sequence_by_part.get(part, 0) + 1
        start = str(pending[0].get("start") or "")
        end = str(pending[-1].get("end") or pending[-1].get("start") or "")
        text = "\n".join(item["raw"] for item in pending).strip()
        chunk_id = f"{bvid}-p{part}-{sequence_by_part[part]:04d}"
        chunks.append({
            "chunk_id": chunk_id,
            "bvid": bvid,
            "source_url": source_url,
            "title": title,
            "author": author,
            "video_type": video_type,
            "part": part,
            "start_time": start,
            "end_time": end,
            "start_seconds": _seconds(start),
            "end_seconds": _seconds(end),
            "time_unavailable": not bool(start),
            "citation": _citation(bvid, part, start, end),
            "text": text,
        })
        pending = []

    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        part_match = PART_RE.match(line)
        if part_match:
            flush()
            part = max(1, int(part_match.group(1)))
            continue
        if line.startswith("#"):
            continue
        time_match = TIME_LINE_RE.match(line)
        if time_match:
            start, end, body = time_match.groups()
            segment = {"start": start.strip(), "end": end.strip(), "raw": line, "body": body.strip()}
        else:
            segment = {"start": "", "end": "", "raw": line, "body": line}
        current_chars = sum(len(item["raw"]) + 1 for item in pending)
        next_chars = len(segment["raw"]) + 1
        if pending and (current_chars >= target_chars or current_chars + next_chars > max_chars):
            flush()
        pending.append(segment)
    flush()
    return chunks


def write_chunks(path: Path, chunks: Iterable[dict[str, Any]]) -> int:
    records = list(chunks)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
        + ("\n" if records else ""),
        encoding="utf-8",
    )
    return len(records)


def _connect(index_path: Path = INDEX_PATH) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(index_path)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        existing = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "videos" in existing:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(videos)")}
            required = {"content_hash", "simhash", "duplicate_of", "near_duplicates"}
            stored_version = ""
            if "index_meta" in existing:
                row = connection.execute(
                    "SELECT value FROM index_meta WHERE key = 'schema_version'"
                ).fetchone()
                stored_version = str(row["value"]) if row else ""
            if not required.issubset(columns) or stored_version != str(SCHEMA_VERSION):
                connection.close()
                index_path.unlink(missing_ok=True)
                return _connect(index_path)
        connection.executescript(
            """
        CREATE TABLE IF NOT EXISTS index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS videos (
            bvid TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            video_type TEXT NOT NULL,
            duration INTEGER,
            published_at TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            simhash INTEGER NOT NULL,
            duplicate_of TEXT NOT NULL DEFAULT '',
            near_duplicates TEXT NOT NULL DEFAULT '[]',
            chunk_count INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            bvid TEXT NOT NULL REFERENCES videos(bvid) ON DELETE CASCADE,
            part INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            start_seconds INTEGER,
            end_seconds INTEGER,
            citation TEXT NOT NULL,
            text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            terms TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chunks_bvid ON chunks(bvid);
        CREATE INDEX IF NOT EXISTS videos_type ON videos(video_type);
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
        return connection
    except Exception:
        connection.close()
        raise


def _fingerprint(job: Path) -> str:
    digest = hashlib.sha256()
    for name in ("metadata.json", "transcript.txt", "chunks.jsonl", "index.md"):
        path = job / name
        if path.is_file():
            stat = path.stat()
            digest.update(f"{name}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
    return digest.hexdigest()


def _video_type(job: Path, metadata: dict[str, Any]) -> str:
    value = str(metadata.get("video_type") or "")
    if value in {"tutorial", "knowledge", "narrative", "commentary", "general"}:
        return value
    index_path = job / "index.md"
    if index_path.is_file():
        match = VIDEO_TYPE_RE.search(index_path.read_text(encoding="utf-8", errors="ignore"))
        if match:
            return match.group(1)
    return "general"


def _load_job(job: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    metadata_path = job / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    bvid = str(metadata.get("bvid") or job.name)
    source_url = str(metadata.get("source_url") or f"https://www.bilibili.com/video/{bvid}/")
    video_type = _video_type(job, metadata)
    transcript_path = job / "transcript.txt"
    if transcript_path.is_file() and transcript_path.stat().st_size:
        source_text = transcript_path.read_text(encoding="utf-8", errors="ignore")
        chunks = build_transcript_chunks(
            source_text,
            bvid=bvid,
            source_url=source_url,
            title=str(metadata.get("title") or bvid),
            author=str(metadata.get("author") or ""),
            video_type=video_type,
        )
    else:
        source_text = ""
        chunks = []
        chunks_path = job / "chunks.jsonl"
        if chunks_path.is_file():
            for line in chunks_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and item.get("text"):
                    item.setdefault("bvid", bvid)
                    item.setdefault("title", str(metadata.get("title") or bvid))
                    item.setdefault("author", str(metadata.get("author") or ""))
                    item.setdefault("video_type", video_type)
                    item.setdefault("part", 1)
                    item.setdefault("start_time", "")
                    item.setdefault("end_time", "")
                    item.setdefault("citation", _citation(bvid, int(item["part"]), item["start_time"], item["end_time"]))
                    chunks.append(item)
        source_text = "\n".join(str(item.get("text") or "") for item in chunks)
    metadata["bvid"] = bvid
    metadata["source_url"] = source_url
    metadata["video_type"] = video_type
    return metadata, chunks, source_text


def index_video(
    job: Path,
    *,
    index_path: Path = INDEX_PATH,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    metadata, chunks, source_text = _load_job(job)
    owns_connection = connection is None
    db = connection or _connect(index_path)
    try:
        if str(metadata.get("content_status") or "sufficient") != "sufficient":
            db.execute("DELETE FROM videos WHERE bvid = ?", (metadata["bvid"],))
            if owns_connection:
                db.commit()
            return {
                "chunks": 0,
                "indexed": False,
                "content_status": str(metadata.get("content_status") or "insufficient"),
                "duplicate_of": "",
                "near_duplicates": [],
            }
        db.execute("SAVEPOINT index_one_video")
        try:
            bvid = metadata["bvid"]
            content_hash = _content_hash(source_text)
            simhash = _simhash(source_text)
            duplicate_row = db.execute(
                """SELECT bvid FROM videos
                   WHERE content_hash = ? AND bvid <> ? AND duplicate_of = ''
                   ORDER BY indexed_at, bvid LIMIT 1""",
                (content_hash, bvid),
            ).fetchone()
            duplicate_of = str(duplicate_row["bvid"]) if duplicate_row else ""
            near_duplicates = []
            if not duplicate_of:
                for row in db.execute(
                    "SELECT bvid, simhash, near_duplicates FROM videos WHERE bvid <> ? AND duplicate_of = ''",
                    (bvid,),
                ):
                    similarity = _simhash_similarity(simhash, int(row["simhash"]))
                    if similarity < 0.90:
                        continue
                    near_duplicates.append({"bvid": row["bvid"], "similarity": round(similarity, 4)})
                    reverse = json.loads(row["near_duplicates"] or "[]")
                    reverse = [item for item in reverse if item.get("bvid") != bvid]
                    reverse.append({"bvid": bvid, "similarity": round(similarity, 4)})
                    db.execute(
                        "UPDATE videos SET near_duplicates = ? WHERE bvid = ?",
                        (json.dumps(reverse, ensure_ascii=False), row["bvid"]),
                    )
            db.execute("DELETE FROM videos WHERE bvid = ?", (bvid,))
            db.execute(
                """INSERT INTO videos
                   (bvid, source_url, title, author, video_type, duration, published_at,
                    fingerprint, content_hash, simhash, duplicate_of, near_duplicates,
                    chunk_count, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    bvid,
                    metadata["source_url"],
                    str(metadata.get("title") or bvid),
                    str(metadata.get("author") or ""),
                    metadata["video_type"],
                    metadata.get("duration"),
                    str(metadata.get("published_at") or ""),
                    _fingerprint(job),
                    content_hash,
                    simhash,
                    duplicate_of,
                    json.dumps(near_duplicates, ensure_ascii=False),
                    0 if duplicate_of else len(chunks),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            for chunk in ([] if duplicate_of else chunks):
                search_text = " ".join(filter(None, (
                    str(chunk.get("title") or metadata.get("title") or ""),
                    str(chunk.get("author") or metadata.get("author") or ""),
                    str(chunk.get("text") or ""),
                )))
                db.execute(
                    """INSERT INTO chunks
                       (chunk_id, bvid, part, start_time, end_time, start_seconds,
                        end_seconds, citation, text, content_hash, terms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk["chunk_id"], bvid, int(chunk.get("part") or 1),
                        str(chunk.get("start_time") or ""), str(chunk.get("end_time") or ""),
                        chunk.get("start_seconds"), chunk.get("end_seconds"),
                        str(chunk.get("citation") or ""), str(chunk.get("text") or ""),
                        _content_hash(str(chunk.get("text") or "")),
                        " ".join(tokenize(search_text)),
                    ),
                )
            db.execute("RELEASE SAVEPOINT index_one_video")
        except Exception:
            db.execute("ROLLBACK TO SAVEPOINT index_one_video")
            db.execute("RELEASE SAVEPOINT index_one_video")
            raise
        if owns_connection:
            db.commit()
        return {
            "chunks": 0 if duplicate_of else len(chunks),
            "duplicate_of": duplicate_of,
            "near_duplicates": near_duplicates,
        }
    finally:
        if owns_connection:
            db.close()


def _open_resilient(index_path: Path) -> sqlite3.Connection:
    try:
        return _connect(index_path)
    except sqlite3.DatabaseError:
        index_path.unlink(missing_ok=True)
        return _connect(index_path)


def ensure_index(*, kb_root: Path = KB_ROOT, index_path: Path = INDEX_PATH) -> dict[str, int]:
    db = _open_resilient(index_path)
    indexed = removed = unchanged = 0
    try:
        existing = {row["bvid"]: row["fingerprint"] for row in db.execute("SELECT bvid, fingerprint FROM videos")}
        present: set[str] = set()
        if kb_root.is_dir():
            for job in sorted(path for path in kb_root.iterdir() if path.is_dir()):
                if not (job / "metadata.json").is_file():
                    continue
                try:
                    metadata = json.loads((job / "metadata.json").read_text(encoding="utf-8"))
                    bvid = str(metadata.get("bvid") or job.name)
                    present.add(bvid)
                    if existing.get(bvid) == _fingerprint(job):
                        unchanged += 1
                        continue
                    index_video(job, index_path=index_path, connection=db)
                    indexed += 1
                except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
                    continue
        for bvid in set(existing) - present:
            db.execute("DELETE FROM videos WHERE bvid = ?", (bvid,))
            removed += 1
        inconsistent = db.execute(
            """SELECT COUNT(*) AS count
               FROM videos duplicate
               LEFT JOIN videos canonical ON canonical.bvid = duplicate.duplicate_of
               WHERE duplicate.duplicate_of <> ''
                 AND (canonical.bvid IS NULL OR canonical.content_hash <> duplicate.content_hash)"""
        ).fetchone()["count"]
        db.commit()
        if removed or inconsistent:
            db.close()
            rebuilt = rebuild_index(kb_root=kb_root, index_path=index_path)
            rebuilt["removed"] = removed
            return rebuilt
        return {"indexed": indexed, "unchanged": unchanged, "removed": removed}
    finally:
        db.close()


def rebuild_index(*, kb_root: Path = KB_ROOT, index_path: Path = INDEX_PATH) -> dict[str, int]:
    index_path.unlink(missing_ok=True)
    return ensure_index(kb_root=kb_root, index_path=index_path)


def remove_from_index(bvid: str, *, index_path: Path = INDEX_PATH) -> bool:
    """Remove a diagnostic or unavailable video from the derived search cache."""
    db = _open_resilient(index_path)
    try:
        cursor = db.execute("DELETE FROM videos WHERE bvid = ?", (str(bvid),))
        db.commit()
        return bool(cursor.rowcount)
    finally:
        db.close()


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _playback_url(source_url: str, part: int, start_seconds: int | None) -> str:
    base = source_url.split("?", 1)[0].rstrip("/") + "/"
    params = [f"p={max(1, int(part))}"]
    if start_seconds is not None:
        params.append(f"t={max(0, int(start_seconds))}")
    return base + "?" + "&".join(params)


def search_knowledge(
    query: str,
    *,
    bvids: list[str] | None = None,
    video_type: str = "",
    author: str = "",
    top_k: int = 6,
    max_per_video: int = 2,
    min_confidence: float = 0.35,
    diversify: bool = True,
    kb_root: Path = KB_ROOT,
    index_path: Path = INDEX_PATH,
    _skip_sync: bool = False,
) -> dict[str, Any]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return {"ok": False, "message": "检索问题不能为空", "results": []}
    sync = {"indexed": 0, "unchanged": 0, "removed": 0} if _skip_sync else ensure_index(
        kb_root=kb_root,
        index_path=index_path,
    )
    db = _open_resilient(index_path)
    try:
        clauses: list[str] = []
        arguments: list[Any] = []
        if bvids:
            clean_bvids = [str(value) for value in bvids if str(value).strip()]
            if clean_bvids:
                clauses.append("v.bvid IN (" + ",".join("?" for _ in clean_bvids) + ")")
                arguments.extend(clean_bvids)
        if video_type:
            clauses.append("v.video_type = ?")
            arguments.append(video_type)
        if author:
            clauses.append("v.author LIKE ?")
            arguments.append(f"%{author}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = list(db.execute(
            """SELECT c.*, v.title, v.author, v.video_type, v.source_url
               FROM chunks c JOIN videos v ON v.bvid = c.bvid""" + where,
            arguments,
        ))
    finally:
        db.close()
    if not rows:
        return {"ok": True, "query": query, "results": [], "searched_chunks": 0, "sync": sync}

    document_terms = [row["terms"].split() for row in rows]
    document_frequency = Counter()
    for terms in document_terms:
        document_frequency.update(set(terms))
    average_length = sum(map(len, document_terms)) / max(len(document_terms), 1)
    query_counts = Counter(query_tokens)
    query_term_set = set(query_tokens)
    scored: list[dict[str, Any]] = []
    for row, terms in zip(rows, document_terms):
        frequencies = Counter(terms)
        length = max(len(terms), 1)
        score = 0.0
        for term, query_weight in query_counts.items():
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            inverse = math.log(1 + (len(rows) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length / max(average_length, 1))
            score += query_weight * inverse * frequency * 2.5 / denominator
        lowered_query = query.lower().strip()
        if lowered_query and lowered_query in row["text"].lower():
            score += 3.0
        title_overlap = len(set(query_tokens) & set(tokenize(row["title"])))
        score += title_overlap * 0.8
        matched_terms = query_term_set & set(terms)
        coverage = len(matched_terms) / max(len(query_term_set), 1)
        enough_terms = (
            (len(query_term_set) == 1 and bool(matched_terms))
            or len(matched_terms) >= 2
            or coverage >= 0.40
        )
        if score > 0 and enough_terms:
            scored.append({
                "raw_score": score,
                "coverage": coverage,
                "row": row,
                "term_set": set(terms),
            })
    top_k = max(1, min(int(top_k), 20))
    max_per_video = max(1, min(int(max_per_video), 5))
    min_confidence = max(0.0, min(float(min_confidence), 1.0))
    highest_score = max((item["raw_score"] for item in scored), default=0.0)
    candidates = []
    for item in scored:
        relative = item["raw_score"] / max(highest_score, 1e-9)
        if relative < 0.20:
            continue
        item["confidence"] = min(1.0, 0.6 * item["coverage"] + 0.4 * relative)
        if item["confidence"] >= min_confidence:
            candidates.append(item)

    selected: list[dict[str, Any]] = []
    per_video: Counter[str] = Counter()
    remaining = sorted(
        candidates,
        key=lambda item: (-item["confidence"], -item["raw_score"], item["row"]["chunk_id"]),
    )
    while remaining and len(selected) < top_k:
        allowed = [item for item in remaining if per_video[item["row"]["bvid"]] < max_per_video]
        if not allowed:
            break
        if diversify and selected:
            def mmr(item: dict[str, Any]) -> tuple[float, float, str]:
                redundancy = max(_jaccard(item["term_set"], prior["term_set"]) for prior in selected)
                new_video_bonus = 0.05 if per_video[item["row"]["bvid"]] == 0 else 0.0
                value = 0.75 * item["confidence"] - 0.25 * redundancy + new_video_bonus
                return value, item["raw_score"], item["row"]["chunk_id"]

            choice = max(allowed, key=mmr)
        else:
            choice = allowed[0]
        selected.append(choice)
        per_video[choice["row"]["bvid"]] += 1
        remaining.remove(choice)

    results = []
    for item in selected:
        row = item["row"]
        results.append({
            "chunk_id": row["chunk_id"],
            "score": round(item["raw_score"], 4),
            "confidence": round(item["confidence"], 4),
            "query_coverage": round(item["coverage"], 4),
            "bvid": row["bvid"],
            "title": row["title"],
            "author": row["author"],
            "video_type": row["video_type"],
            "part": row["part"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "citation": row["citation"],
            "source_url": row["source_url"],
            "playback_url": _playback_url(row["source_url"], row["part"], row["start_seconds"]),
            "text": row["text"],
        })
    return {
        "ok": True,
        "query": query,
        "results": results,
        "searched_chunks": len(rows),
        "sync": sync,
    }


def _safe_bvid(value: str) -> str:
    bvid = str(value or "").strip()
    if not BVID_RE.fullmatch(bvid):
        raise ValueError("需要合法的 BV 号")
    return bvid


def _trash_records(kb_root: Path = KB_ROOT) -> list[dict[str, Any]]:
    records = []
    trash_root = kb_root / ".trash"
    if not trash_root.is_dir():
        return records
    for directory in sorted(path for path in trash_root.iterdir() if path.is_dir()):
        manifest = directory / "trash.json"
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            records.append({"trash_id": directory.name, "status": "corrupt"})
            continue
        payload["trash_id"] = directory.name
        payload["status"] = "trashed"
        records.append(payload)
    return records


def forget_video(
    bvid: str,
    *,
    reason: str = "",
    kb_root: Path = KB_ROOT,
    index_path: Path = INDEX_PATH,
) -> dict[str, Any]:
    bvid = _safe_bvid(bvid)
    source = kb_root / bvid
    if not source.is_dir():
        raise FileNotFoundError(f"知识库中不存在视频：{bvid}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trash_id = f"{bvid}-{stamp}-{time.time_ns() % 1_000_000:06d}"
    trash_root = kb_root / ".trash"
    trash_root.mkdir(parents=True, exist_ok=True)
    target = trash_root / trash_id
    shutil.move(str(source), str(target))
    manifest = {
        "bvid": bvid,
        "original_path": str(Path("knowledge_base") / bvid),
        "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": " ".join(str(reason or "").split())[:500],
    }
    target.joinpath("trash.json").write_text(_json(manifest) + "\n", encoding="utf-8")
    rebuild_index(kb_root=kb_root, index_path=index_path)
    return {"ok": True, "trash_id": trash_id, **manifest}


def restore_video(
    trash_id: str,
    *,
    kb_root: Path = KB_ROOT,
    index_path: Path = INDEX_PATH,
) -> dict[str, Any]:
    clean_id = Path(str(trash_id or "")).name
    if not clean_id or clean_id != str(trash_id):
        raise ValueError("无效 trash_id")
    source = kb_root / ".trash" / clean_id
    manifest_path = source / "trash.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"回收记录损坏，无法恢复：{clean_id}") from exc
    bvid = _safe_bvid(str(manifest.get("bvid") or ""))
    target = kb_root / bvid
    if target.exists():
        raise FileExistsError(f"恢复目标已存在，拒绝覆盖：{target}")
    shutil.move(str(source), str(target))
    target.joinpath("trash.json").unlink(missing_ok=True)
    rebuild_index(kb_root=kb_root, index_path=index_path)
    return {"ok": True, "trash_id": clean_id, "bvid": bvid, "restored_path": str(target)}


def purge_trash(
    trash_id: str = "",
    *,
    all: bool = False,
    kb_root: Path = KB_ROOT,
) -> dict[str, Any]:
    trash_root = kb_root / ".trash"
    if not trash_root.is_dir():
        return {"ok": True, "purged": []}
    if all:
        targets = [path for path in trash_root.iterdir() if path.is_dir()]
    else:
        clean_id = Path(str(trash_id or "")).name
        if not clean_id or clean_id != str(trash_id):
            raise ValueError("必须提供合法 trash_id，或显式设置 all=true")
        target = trash_root / clean_id
        if not target.is_dir():
            raise FileNotFoundError(f"回收区中不存在：{clean_id}")
        targets = [target]
    purged = []
    for target in targets:
        purged.append(target.name)
        shutil.rmtree(target)
    return {"ok": True, "purged": sorted(purged)}


def export_knowledge(
    bvids: list[str] | None = None,
    *,
    output_path: str = "",
    kb_root: Path = KB_ROOT,
) -> dict[str, Any]:
    selected = {_safe_bvid(value) for value in (bvids or [])}
    jobs = [
        path for path in sorted(kb_root.iterdir())
        if path.is_dir() and path.name != ".trash" and (not selected or path.name in selected)
    ] if kb_root.is_dir() else []
    missing = sorted(selected - {path.name for path in jobs})
    if missing:
        raise FileNotFoundError("未找到待导出视频：" + ", ".join(missing))
    if not jobs:
        raise ValueError("没有可导出的 active 视频知识库")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    relative_output = output_path or f"exports/video-knowledge-{stamp}.zip"
    destination = workspace_path(relative_output)
    if destination.suffix.lower() != ".zip":
        raise ValueError("知识库导出路径必须使用 .zip 后缀")
    destination.parent.mkdir(parents=True, exist_ok=True)
    allowed_names = {"metadata.json", "index.md", "chunks.jsonl", "transcript.txt", "visual_notes.jsonl"}
    exported_files = []
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for job in jobs:
            files = [path for path in job.iterdir() if path.is_file()]
            files = [
                path for path in files
                if path.name in allowed_names or re.fullmatch(r"transcript_p\d+\.txt", path.name)
            ]
            for path in sorted(files):
                arcname = str(Path("knowledge_base") / job.name / path.name)
                archive.write(path, arcname)
                exported_files.append(arcname)
        archive.writestr("manifest.json", _json({
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "videos": [path.name for path in jobs],
            "files": exported_files,
        }) + "\n")
    return {
        "ok": True,
        "output_path": str(destination.relative_to(Path.cwd().resolve())),
        "videos": [path.name for path in jobs],
        "files": len(exported_files),
        "bytes": destination.stat().st_size,
    }


def catalog_knowledge(
    query: str = "",
    *,
    video_type: str = "",
    author: str = "",
    limit: int = 50,
    kb_root: Path = KB_ROOT,
    index_path: Path = INDEX_PATH,
) -> dict[str, Any]:
    sync = ensure_index(kb_root=kb_root, index_path=index_path)
    db = _open_resilient(index_path)
    try:
        rows = list(db.execute("SELECT * FROM videos ORDER BY indexed_at DESC, bvid"))
    finally:
        db.close()
    query_terms = set(tokenize(query))
    selected = []
    counts = Counter(str(row["video_type"]) for row in rows)
    diagnostic_count = 0
    for row in rows:
        if video_type and row["video_type"] != video_type:
            continue
        if author and author.lower() not in row["author"].lower():
            continue
        if query_terms and not query_terms.intersection(tokenize(f"{row['title']} {row['author']}")):
            continue
        item = dict(row)
        item["near_duplicates"] = json.loads(item.get("near_duplicates") or "[]")
        item["status"] = "duplicate" if item.get("duplicate_of") else "active"
        selected.append(item)
    indexed_bvids = {str(row["bvid"]) for row in rows}
    if kb_root.is_dir():
        for job in sorted(path for path in kb_root.iterdir() if path.is_dir() and path.name != ".trash"):
            metadata_path = job / "metadata.json"
            if not metadata_path.is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            bvid = str(metadata.get("bvid") or job.name)
            if bvid in indexed_bvids or metadata.get("content_status") != "insufficient":
                continue
            item = {
                "bvid": bvid,
                "source_url": str(metadata.get("source_url") or ""),
                "title": str(metadata.get("title") or bvid),
                "author": str(metadata.get("author") or ""),
                "video_type": str(metadata.get("video_type") or "general"),
                "chunk_count": 0,
                "duplicate_of": "",
                "near_duplicates": [],
                "status": "diagnostic",
                "content_status": "insufficient",
                "content_reason": str(metadata.get("content_reason") or ""),
            }
            diagnostic_count += 1
            counts[item["video_type"]] += 1
            if video_type and item["video_type"] != video_type:
                continue
            if author and author.lower() not in item["author"].lower():
                continue
            if query_terms and not query_terms.intersection(tokenize(f"{item['title']} {item['author']}")):
                continue
            selected.append(item)
    trash = _trash_records(kb_root)
    limit = max(1, min(int(limit), 200))
    return {
        "ok": True,
        "video_count": len(rows) + diagnostic_count,
        "chunk_count": sum(int(row["chunk_count"]) for row in rows),
        "active_count": sum(not bool(row["duplicate_of"]) for row in rows),
        "duplicate_count": sum(bool(row["duplicate_of"]) for row in rows),
        "diagnostic_count": diagnostic_count,
        "trashed_count": len(trash),
        "video_types": dict(sorted(counts.items())),
        "videos": selected[:limit],
        "trash": trash[:limit],
        "index": {"schema_version": SCHEMA_VERSION, "path": str(index_path), "healthy": True},
        "sync": sync,
    }


def _kb_search(
    query: str = "",
    bvids: list[str] | None = None,
    video_type: str = "",
    author: str = "",
    top_k: int = 6,
    max_per_video: int = 2,
    min_confidence: float = 0.35,
    diversify: bool = True,
) -> str:
    return _json(search_knowledge(
        query,
        bvids=bvids,
        video_type=video_type,
        author=author,
        top_k=top_k,
        max_per_video=max_per_video,
        min_confidence=min_confidence,
        diversify=diversify,
    ))


def _kb_catalog(query: str = "", video_type: str = "", author: str = "", limit: int = 50) -> str:
    return _json(catalog_knowledge(query, video_type=video_type, author=author, limit=limit))


def _kb_forget(bvid: str = "", reason: str = "") -> str:
    return _json(forget_video(bvid, reason=reason))


def _kb_restore(trash_id: str = "") -> str:
    return _json(restore_video(trash_id))


def _kb_export(bvids: list[str] | None = None, output_path: str = "") -> str:
    return _json(export_knowledge(bvids, output_path=output_path))


def _kb_purge_trash(trash_id: str = "", all: bool = False) -> str:
    return _json(purge_trash(trash_id, all=all))


kb_search_tool = Tool(
    "kb_search",
    "检索用户历次提炼的个人视频知识库，返回相关原文、视频标题、BV、分P和时间位置。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "bvids": {"type": "array", "items": {"type": "string"}},
            "video_type": {"type": "string", "enum": ["", "tutorial", "knowledge", "narrative", "commentary", "general"]},
            "author": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 6},
            "max_per_video": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
            "min_confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.35},
            "diversify": {"type": "boolean", "default": True},
        },
        "required": ["query"],
    },
    _kb_search,
)

kb_catalog_tool = Tool(
    "kb_catalog",
    "查看个人视频知识库的规模、类型分布和已收录视频，可按标题、作者或类型筛选。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "video_type": {"type": "string", "enum": ["", "tutorial", "knowledge", "narrative", "commentary", "general"]},
            "author": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
    },
    _kb_catalog,
)

kb_forget_tool = Tool(
    "kb_forget",
    "把一个视频知识库软删除到可恢复回收区，并立即从检索索引移除。",
    {
        "type": "object",
        "properties": {"bvid": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["bvid"],
    },
    _kb_forget,
)

kb_restore_tool = Tool(
    "kb_restore",
    "按 trash_id 恢复软删除的视频知识库；目标 BV 已存在时拒绝覆盖。",
    {
        "type": "object",
        "properties": {"trash_id": {"type": "string"}},
        "required": ["trash_id"],
    },
    _kb_restore,
)

kb_export_tool = Tool(
    "kb_export",
    "导出可迁移的视频知识 ZIP；只包含知识文本，不包含媒体、密钥、trace 或派生索引。",
    {
        "type": "object",
        "properties": {
            "bvids": {"type": "array", "items": {"type": "string"}},
            "output_path": {"type": "string"},
        },
    },
    _kb_export,
)

kb_purge_trash_tool = Tool(
    "kb_purge_trash",
    "永久清理知识库回收区；不可恢复，默认必须指定 trash_id。",
    {
        "type": "object",
        "properties": {
            "trash_id": {"type": "string"},
            "all": {"type": "boolean", "default": False},
        },
    },
    _kb_purge_trash,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain the personal video knowledge index")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="list active, duplicate and trashed knowledge")
    search_parser = subparsers.add_parser("search", help="search the personal knowledge base")
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=6)
    subparsers.add_parser("reindex", help="rebuild the derived SQLite index")
    forget_parser = subparsers.add_parser("forget", help="soft-delete one BV")
    forget_parser.add_argument("bvid")
    forget_parser.add_argument("--reason", default="")
    restore_parser = subparsers.add_parser("restore", help="restore one trash entry")
    restore_parser.add_argument("trash_id")
    export_parser = subparsers.add_parser("export", help="export portable knowledge ZIP")
    export_parser.add_argument("--bvid", action="append", dest="bvids")
    export_parser.add_argument("--output", default="")
    purge_parser = subparsers.add_parser("purge", help="permanently purge trash")
    purge_parser.add_argument("trash_id", nargs="?", default="")
    purge_parser.add_argument("--all", action="store_true")
    parser.add_argument("--reindex", action="store_true", help="rebuild the derived SQLite index")
    parser.add_argument("--query", default="", help="run a retrieval query after indexing")
    args = parser.parse_args(argv)
    if args.command == "list":
        result = catalog_knowledge()
    elif args.command == "search":
        result = search_knowledge(args.query, top_k=args.top_k)
    elif args.command == "reindex" or args.reindex:
        result = rebuild_index()
    elif args.command == "forget":
        result = forget_video(args.bvid, reason=args.reason)
    elif args.command == "restore":
        result = restore_video(args.trash_id)
    elif args.command == "export":
        result = export_knowledge(args.bvids, output_path=args.output)
    elif args.command == "purge":
        result = purge_trash(args.trash_id, all=args.all)
    elif args.query:
        result = search_knowledge(args.query)
    else:
        result = ensure_index()
    print(_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
