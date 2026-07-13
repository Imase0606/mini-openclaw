"""Explicit Bilibili QR login and local credential storage."""
from __future__ import annotations

import argparse
import http.cookiejar
import io
import json
import os
import stat
import time
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
KEYRING_SERVICE = "mini-openclaw"
KEYRING_USER = "bilibili-session"
COOKIE_NAMES = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 mini-openclaw",
    "Referer": "https://www.bilibili.com/",
}


def auth_root() -> Path:
    configured = os.getenv("MINI_OPENCLAW_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".mini-openclaw"


def session_path() -> Path:
    return auth_root() / "secrets" / "bilibili_session.json"


def _keyring() -> Any | None:
    try:
        import keyring
        backend = keyring.get_keyring()
        if float(getattr(backend, "priority", 0) or 0) <= 0:
            return None
        return keyring
    except Exception:
        return None


def _cookie_records(cookies: httpx.Cookies) -> list[dict[str, Any]]:
    records = []
    for cookie in cookies.jar:
        if cookie.name not in COOKIE_NAMES or "bilibili.com" not in cookie.domain:
            continue
        records.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path or "/",
            "expires": cookie.expires,
            "secure": bool(cookie.secure),
        })
    return records


def _serialize(cookies: httpx.Cookies) -> str:
    records = _cookie_records(cookies)
    if not any(item["name"] == "SESSDATA" for item in records):
        raise ValueError("扫码成功响应未包含可用的 B站登录会话")
    return json.dumps({"version": 1, "cookies": records}, ensure_ascii=False)


def save_session(cookies: httpx.Cookies) -> str:
    payload = _serialize(cookies)
    keyring = _keyring()
    if keyring is not None:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, payload)
            session_path().unlink(missing_ok=True)
            return "keyring"
        except Exception:
            pass
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload + "\n", encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(path)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)
        except (AttributeError, OSError):
            pass
    return "file"


def _read_payload() -> tuple[str, str]:
    keyring = _keyring()
    if keyring is not None:
        try:
            value = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if value:
                return value, "keyring"
        except Exception:
            pass
    path = session_path()
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
        return "", ""
    return path.read_text(encoding="utf-8"), "file"


def _cookies_from_payload(raw: str) -> httpx.Cookies:
    payload = json.loads(raw)
    if payload.get("version") != 1 or not isinstance(payload.get("cookies"), list):
        raise ValueError("B站登录会话格式无效")
    cookies = httpx.Cookies()
    for item in payload["cookies"]:
        if not isinstance(item, dict) or item.get("name") not in COOKIE_NAMES:
            continue
        cookies.set(
            str(item["name"]),
            str(item.get("value") or ""),
            domain=str(item.get("domain") or ".bilibili.com"),
            path=str(item.get("path") or "/"),
        )
    return cookies


def _cookies_from_netscape(path_value: str) -> httpx.Cookies:
    path = Path(path_value).expanduser()
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
        raise ValueError("BILIBILI_COOKIE_FILE 不可读、不是普通文件或超过 1 MiB")
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    cookies = httpx.Cookies()
    for cookie in jar:
        if cookie.name in COOKIE_NAMES and "bilibili.com" in cookie.domain:
            cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path or "/")
    return cookies


def load_session() -> tuple[httpx.Cookies, str]:
    advanced = os.getenv("BILIBILI_COOKIE_FILE", "").strip()
    if advanced:
        try:
            return _cookies_from_netscape(advanced), "cookie_file"
        except Exception:
            return httpx.Cookies(), "cookie_file_error"
    raw, storage = _read_payload()
    if not raw:
        return httpx.Cookies(), ""
    try:
        return _cookies_from_payload(raw), storage
    except (ValueError, json.JSONDecodeError, OSError):
        return httpx.Cookies(), "corrupt"


@contextmanager
def temporary_netscape_cookie_file():
    """Materialize the stored session only for an authenticated yt-dlp subtitle call."""
    cookies, _storage = load_session()
    records = _cookie_records(cookies)
    if not records:
        yield ""
        return
    with tempfile.TemporaryDirectory(prefix="mini-openclaw-bili-auth-") as tmp:
        path = Path(tmp) / "cookies.txt"
        lines = ["# Netscape HTTP Cookie File"]
        for item in records:
            domain = str(item["domain"])
            lines.append("\t".join((
                domain,
                "TRUE" if domain.startswith(".") else "FALSE",
                str(item.get("path") or "/"),
                "TRUE" if item.get("secure") else "FALSE",
                str(int(item.get("expires") or 0)),
                str(item["name"]),
                str(item["value"]),
            )))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
        yield str(path)


def logout() -> None:
    keyring = _keyring()
    if keyring is not None:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:
            pass
    session_path().unlink(missing_ok=True)


def auth_status(*, client: httpx.Client | None = None) -> dict[str, Any]:
    cookies, storage = load_session()
    if storage in {"corrupt", "cookie_file_error"}:
        return {"status": "error"}
    if not any(cookie.name == "SESSDATA" for cookie in cookies.jar):
        return {"status": "not_logged_in"}
    owns_client = client is None
    session = client or httpx.Client(headers=HEADERS, cookies=cookies, timeout=20)
    try:
        response = session.get(NAV_URL)
        response.raise_for_status()
        payload = response.json()
        valid = payload.get("code") == 0 and bool((payload.get("data") or {}).get("isLogin"))
        return {"status": "valid" if valid else "expired"}
    except Exception:
        return {"status": "error"}
    finally:
        if owns_client:
            session.close()


@dataclass
class QRChallenge:
    url: str
    key: str
    client: httpx.Client


def begin_qr_login(*, client: httpx.Client | None = None) -> QRChallenge:
    session = client or httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True)
    response = session.get(QR_GENERATE_URL)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    if payload.get("code") != 0 or not data.get("url") or not data.get("qrcode_key"):
        if client is None:
            session.close()
        raise RuntimeError("B站二维码接口未返回有效挑战")
    return QRChallenge(str(data["url"]), str(data["qrcode_key"]), session)


def poll_qr_login(challenge: QRChallenge, *, timeout: int = 180, interval: float = 2.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(10, int(timeout))
    last_status = "waiting_scan"
    try:
        while time.monotonic() < deadline:
            result = poll_qr_once(challenge)
            if result["status"] in {"success", "expired", "error"}:
                return result
            last_status = str(result["status"])
            time.sleep(max(0.2, interval))
        return {"status": "timeout", "last_status": last_status}
    finally:
        challenge.client.close()


def poll_qr_once(challenge: QRChallenge) -> dict[str, Any]:
    try:
        response = challenge.client.get(QR_POLL_URL, params={"qrcode_key": challenge.key})
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        code = int(data.get("code") if data.get("code") is not None else -1)
        if payload.get("code") != 0:
            return {"status": "error"}
        if code == 0:
            return {"status": "success", "storage": save_session(challenge.client.cookies)}
        if code == 86038:
            return {"status": "expired"}
        if code == 86090:
            return {"status": "scanned_waiting_confirmation"}
        return {"status": "waiting_scan"}
    except Exception:
        return {"status": "error"}


def render_qr_ascii(url: str) -> str:
    try:
        import qrcode
    except ImportError as exc:
        raise RuntimeError("缺少 qrcode 依赖，无法显示登录二维码") from exc
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    stream = io.StringIO()
    qr.print_ascii(out=stream, tty=False, invert=True)
    return stream.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the local Bilibili subtitle login")
    parser.add_argument("command", choices=("login", "status", "logout"))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    if args.command == "logout":
        logout()
        print(json.dumps({"status": "logged_out"}, ensure_ascii=False))
        return 0
    if args.command == "status":
        result = auth_status()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "valid" else 1
    try:
        challenge = begin_qr_login()
        print("请使用手机 B站扫描二维码并确认登录：")
        print(render_qr_ascii(challenge.url))
        result = poll_qr_login(challenge, timeout=args.timeout)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
