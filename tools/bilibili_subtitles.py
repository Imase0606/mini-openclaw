"""Bilibili anonymous/authenticated subtitle discovery and audit CLI."""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .bilibili_auth import (
    HEADERS,
    auth_status,
    bind_auth_session,
    create_auth_session,
    interactive_login,
    load_session,
)


PLAYER_URL = "https://api.bilibili.com/x/player/v2"
LANGUAGE_ORDER = ("zh-CN", "zh-Hans", "zh-Hant", "zh", "ai-zh", "en", "ai-en")
AUTHENTICATED_SUBTITLE_ATTEMPTS = 10


@dataclass
class SubtitleResult:
    status: str
    source: str = ""
    language: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    auth_status: str = "not_logged_in"
    auth_used: bool = False
    fallback_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "subtitle_status": self.status,
            "subtitle_source": self.source,
            "subtitle_language": self.language,
            "auth_status": self.auth_status,
            "auth_used": self.auth_used,
            "fallback_reason": self.fallback_reason,
            "segments": self.segments,
        }


def _language_rank(language: str) -> tuple[int, str]:
    normalized = str(language or "")
    try:
        return LANGUAGE_ORDER.index(normalized), normalized
    except ValueError:
        if "zh" in normalized.lower():
            return len(LANGUAGE_ORDER), normalized
        if "en" in normalized.lower():
            return len(LANGUAGE_ORDER) + 1, normalized
        return len(LANGUAGE_ORDER) + 2, normalized


def _subtitle_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    subtitle = data.get("subtitle") or {}
    entries = subtitle.get("subtitles") or []
    return sorted(
        [item for item in entries if isinstance(item, dict) and item.get("subtitle_url")],
        key=lambda item: _language_rank(str(item.get("lan") or item.get("lan_doc") or "")),
    )


def _segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for item in payload.get("body") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        records.append({
            "start": float(item.get("from") or 0),
            "end": float(item.get("to") or item.get("from") or 0),
            "text": text,
        })
    return records


def _request_with_client(
    client: httpx.Client,
    *,
    bvid: str,
    cid: int | str,
    authenticated: bool,
    auth_state: str,
) -> SubtitleResult | None:
    response = client.get(
        PLAYER_URL,
        params={"bvid": bvid, "cid": cid, "_": time.time_ns()},
        headers={**HEADERS, "Referer": f"https://www.bilibili.com/video/{bvid}/"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError("B站播放器接口拒绝字幕查询")
    entries = _subtitle_entries(payload)
    if not entries:
        return None
    entry = entries[0]
    language = str(entry.get("lan") or entry.get("lan_doc") or "unknown")
    subtitle_url = str(entry["subtitle_url"])
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    elif not subtitle_url.startswith(("http://", "https://")):
        subtitle_url = urljoin("https://www.bilibili.com/", subtitle_url)
    subtitle_response = client.get(subtitle_url, headers=HEADERS)
    subtitle_response.raise_for_status()
    segments = _segments(subtitle_response.json())
    if not segments:
        raise RuntimeError("B站字幕文件不包含有效片段")
    return SubtitleResult(
        status="authenticated_found" if authenticated else "anonymous_found",
        source="bilibili_player_api",
        language=language,
        segments=segments,
        auth_status=auth_state,
        auth_used=authenticated,
    )


def _subtitle_end(result: SubtitleResult) -> float:
    return max((float(item.get("end") or 0) for item in result.segments), default=0.0)


def _plausible_for_duration(result: SubtitleResult, expected_duration: float | int | None) -> bool:
    duration = float(expected_duration or 0)
    if duration <= 0:
        return bool(result.segments)
    end = _subtitle_end(result)
    return duration * 0.8 <= end <= duration * 1.1 + 10


def fetch_subtitles(
    bvid: str,
    cid: int | str,
    *,
    anonymous_client: httpx.Client | None = None,
    authenticated_client: httpx.Client | None = None,
    auth_state: dict[str, Any] | None = None,
    expected_duration: float | int | None = None,
) -> SubtitleResult:
    anonymous_owned = anonymous_client is None
    anonymous = anonymous_client or httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)
    anonymous_error = ""
    try:
        try:
            result = _request_with_client(
                anonymous, bvid=bvid, cid=cid, authenticated=False,
                auth_state="not_required",
            )
            if result and _plausible_for_duration(result, expected_duration):
                return result
        except Exception as exc:
            anonymous_error = f"{type(exc).__name__}"
    finally:
        if anonymous_owned:
            anonymous.close()

    state = auth_state or auth_status()
    status = str(state.get("status") or "error")
    if status != "valid":
        mapped = {
            "not_logged_in": "auth_not_configured",
            "expired": "auth_expired",
            "error": "error",
        }.get(status, "error")
        return SubtitleResult(
            status=mapped,
            auth_status=status,
            fallback_reason=anonymous_error or "anonymous_subtitle_not_found",
        )

    cookies, _storage = load_session()
    authenticated_owned = authenticated_client is None
    authenticated = authenticated_client or httpx.Client(
        headers=HEADERS, cookies=cookies, timeout=30, follow_redirects=True,
    )
    try:
        best_result: SubtitleResult | None = None
        last_error: Exception | None = None
        saw_subtitle = False
        attempts = AUTHENTICATED_SUBTITLE_ATTEMPTS if expected_duration else 3
        for attempt in range(attempts):
            try:
                result = _request_with_client(
                    authenticated, bvid=bvid, cid=cid, authenticated=True, auth_state="valid",
                )
            except Exception as exc:  # Keep a successful attempt if a later request is transiently broken.
                last_error = exc
                result = None
            if result:
                saw_subtitle = True
                if _plausible_for_duration(result, expected_duration):
                    if not expected_duration:
                        if best_result is None or len(result.segments) > len(best_result.segments):
                            best_result = result
                    elif best_result is None or abs(
                        _subtitle_end(result) - float(expected_duration)
                    ) < abs(_subtitle_end(best_result) - float(expected_duration)):
                        best_result = result
                    coverage = _subtitle_end(result) / float(expected_duration or 1)
                    if expected_duration and 0.9 <= coverage <= 1.05:
                        return result
            if attempt + 1 < attempts:
                time.sleep(0.25)
        if best_result:
            return best_result
        if last_error is not None:
            raise last_error
        if saw_subtitle:
            return SubtitleResult(
                status="error",
                auth_status="valid",
                auth_used=True,
                fallback_reason="authenticated_subtitle_incomplete",
            )
        return SubtitleResult(
            status="not_found",
            auth_status="valid",
            auth_used=True,
            fallback_reason="authenticated_subtitle_not_found",
        )
    except Exception as exc:
        return SubtitleResult(
            status="error",
            auth_status="valid",
            auth_used=True,
            fallback_reason=f"authenticated_subtitle_error:{type(exc).__name__}",
        )
    finally:
        if authenticated_owned:
            authenticated.close()


def audit_bvid(bvid: str) -> dict[str, Any]:
    from .video import _metadata_from_bili_api

    metadata = _metadata_from_bili_api(f"https://www.bilibili.com/video/{bvid}/")
    pages = metadata.get("pages") or [{"cid": metadata.get("cid"), "page": 1}]
    results = []
    for page in pages:
        result = fetch_subtitles(
            bvid,
            page.get("cid") or metadata.get("cid"),
            expected_duration=page.get("duration") or metadata.get("duration"),
        )
        data = result.as_dict()
        data.pop("segments", None)
        data["page"] = int(page.get("page") or 1)
        results.append(data)
    return {"bvid": bvid, "title": metadata.get("title") or bvid, "parts": results}


def _workspace_bvids() -> list[str]:
    root = Path("knowledge_base")
    return sorted(
        path.name for path in root.iterdir()
        if path.is_dir() and path.name.startswith("BV") and (path / "metadata.json").is_file()
    ) if root.is_dir() else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Bilibili subtitle availability without modifying knowledge")
    parser.add_argument("command", choices=("audit",), default="audit", nargs="?")
    parser.add_argument("--bvid", action="append", dest="bvids")
    parser.add_argument(
        "--bilibili-login",
        action="store_true",
        help="scan and audit within the same ephemeral process",
    )
    args = parser.parse_args(argv)
    bvids = args.bvids or _workspace_bvids()
    if not bvids:
        print(json.dumps({"ok": False, "message": "没有可审计的 BV"}, ensure_ascii=False))
        return 1
    auth_session = create_auth_session()
    records = []
    try:
        if args.bilibili_login:
            login_result = interactive_login(session=auth_session)
            if login_result.get("status") != "success":
                print(json.dumps({
                    "ok": False,
                    "message": f"B站扫码登录未完成：{login_result.get('status')}",
                }, ensure_ascii=False))
                return 1
        with bind_auth_session(auth_session):
            for bvid in bvids:
                try:
                    records.append(audit_bvid(bvid))
                except Exception as exc:
                    records.append({"bvid": bvid, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        auth_session.close()
    print(json.dumps({"ok": all("error" not in item for item in records), "videos": records}, ensure_ascii=False, indent=2))
    return 0 if all("error" not in item for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
