"""Explicit Bilibili QR login with persistent or Runtime-scoped storage."""
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
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
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
AUTH_MODES = {"persistent", "ephemeral", "disabled"}
EPHEMERAL_TTL_SECONDS = 30 * 60


def auth_mode() -> str:
    value = os.getenv("BILIBILI_AUTH_MODE", "persistent").strip().lower()
    return value if value in AUTH_MODES else "disabled"


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


def _save_persistent_session(cookies: httpx.Cookies) -> str:
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


def _load_persistent_session() -> tuple[httpx.Cookies, str]:
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


def clear_persistent_session() -> None:
    keyring = _keyring()
    if keyring is not None:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:
            pass
    session_path().unlink(missing_ok=True)


@dataclass
class BilibiliAuthSession:
    mode: str
    ttl_seconds: int = EPHEMERAL_TTL_SECONDS

    def __post_init__(self) -> None:
        self.mode = self.mode if self.mode in AUTH_MODES else "disabled"
        self._payload = ""
        self._expires_at = 0.0
        self._lock = RLock()

    def save(self, cookies: httpx.Cookies) -> str:
        if self.mode == "disabled":
            raise RuntimeError("当前部署已禁用 B站登录")
        if self.mode == "persistent":
            return _save_persistent_session(cookies)
        payload = _serialize(cookies)
        with self._lock:
            self._payload = payload
            self._expires_at = time.monotonic() + self.ttl_seconds
        return "ephemeral"

    def load(self) -> tuple[httpx.Cookies, str]:
        if self.mode == "disabled":
            return httpx.Cookies(), "disabled"
        if self.mode == "persistent":
            return _load_persistent_session()
        with self._lock:
            if not self._payload:
                return httpx.Cookies(), "ephemeral"
            if time.monotonic() >= self._expires_at:
                self._payload = ""
                self._expires_at = 0.0
                return httpx.Cookies(), "expired"
            payload = self._payload
        return _cookies_from_payload(payload), "ephemeral"

    def expires_in_seconds(self) -> int | None:
        if self.mode != "ephemeral":
            return None
        with self._lock:
            if not self._payload:
                return 0
            remaining = max(0, int(self._expires_at - time.monotonic()))
            if remaining == 0:
                self._payload = ""
                self._expires_at = 0.0
            return remaining

    def logout(self) -> None:
        if self.mode == "persistent":
            clear_persistent_session()
            return
        with self._lock:
            self._payload = ""
            self._expires_at = 0.0

    def close(self) -> None:
        if self.mode == "ephemeral":
            self.logout()


_ACTIVE_SESSION: ContextVar[BilibiliAuthSession | None] = ContextVar(
    "bilibili_auth_session", default=None,
)


def create_auth_session(mode: str | None = None) -> BilibiliAuthSession:
    session = BilibiliAuthSession(mode or auth_mode())
    if session.mode == "ephemeral":
        clear_persistent_session()
    return session


@contextmanager
def bind_auth_session(session: BilibiliAuthSession):
    token = _ACTIVE_SESSION.set(session)
    try:
        yield session
    finally:
        _ACTIVE_SESSION.reset(token)


def _resolve_session(session: BilibiliAuthSession | None) -> BilibiliAuthSession | None:
    return session or _ACTIVE_SESSION.get()


def save_session(cookies: httpx.Cookies, *, session: BilibiliAuthSession | None = None) -> str:
    current = _resolve_session(session)
    if current is not None:
        return current.save(cookies)
    mode = auth_mode()
    if mode == "persistent":
        return _save_persistent_session(cookies)
    if mode == "disabled":
        raise RuntimeError("当前部署已禁用 B站登录")
    raise RuntimeError("ephemeral 模式必须在当前 TUI/CLI Runtime 内扫码")


def load_session(*, session: BilibiliAuthSession | None = None) -> tuple[httpx.Cookies, str]:
    current = _resolve_session(session)
    if current is not None:
        return current.load()
    mode = auth_mode()
    if mode == "persistent":
        return _load_persistent_session()
    return httpx.Cookies(), mode


@contextmanager
def temporary_netscape_cookie_file(*, session: BilibiliAuthSession | None = None):
    """Materialize the active session only for an authenticated yt-dlp subtitle call."""
    cookies, _storage = load_session(session=session)
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


def logout(*, session: BilibiliAuthSession | None = None) -> None:
    current = _resolve_session(session)
    if current is not None:
        current.logout()
    elif auth_mode() in {"persistent", "ephemeral"}:
        clear_persistent_session()


def auth_status(
    *,
    client: httpx.Client | None = None,
    session: BilibiliAuthSession | None = None,
) -> dict[str, Any]:
    current = _resolve_session(session)
    mode = current.mode if current is not None else auth_mode()
    cookies, storage = load_session(session=current)
    expires_in = current.expires_in_seconds() if current is not None else None
    if storage in {"corrupt", "cookie_file_error"}:
        return {"mode": mode, "status": "error", "expires_in_seconds": expires_in}
    if storage == "expired":
        return {"mode": mode, "status": "expired", "expires_in_seconds": 0}
    if not any(cookie.name == "SESSDATA" for cookie in cookies.jar):
        return {"mode": mode, "status": "not_logged_in", "expires_in_seconds": expires_in}
    owns_client = client is None
    session = client or httpx.Client(headers=HEADERS, cookies=cookies, timeout=20)
    try:
        response = session.get(NAV_URL)
        response.raise_for_status()
        payload = response.json()
        valid = payload.get("code") == 0 and bool((payload.get("data") or {}).get("isLogin"))
        if not valid and current is not None and current.mode == "ephemeral":
            current.logout()
        return {
            "mode": mode,
            "status": "valid" if valid else "expired",
            "expires_in_seconds": current.expires_in_seconds() if current is not None else None,
        }
    except Exception:
        return {"mode": mode, "status": "error", "expires_in_seconds": expires_in}
    finally:
        if owns_client:
            session.close()


@dataclass
class QRChallenge:
    url: str
    key: str
    client: httpx.Client


def begin_qr_login(
    *,
    client: httpx.Client | None = None,
    session: BilibiliAuthSession | None = None,
) -> QRChallenge:
    current = _resolve_session(session)
    mode = current.mode if current is not None else auth_mode()
    if mode == "disabled":
        raise RuntimeError("当前部署已禁用 B站登录")
    if mode == "ephemeral" and current is None:
        raise RuntimeError("ephemeral 模式必须在当前 TUI/CLI Runtime 内扫码")
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


def poll_qr_login(
    challenge: QRChallenge,
    *,
    timeout: int = 180,
    interval: float = 2.0,
    session: BilibiliAuthSession | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(10, int(timeout))
    last_status = "waiting_scan"
    try:
        while time.monotonic() < deadline:
            result = poll_qr_once(challenge, session=session)
            if result["status"] in {"success", "expired", "error"}:
                return result
            last_status = str(result["status"])
            time.sleep(max(0.2, interval))
        return {"status": "timeout", "last_status": last_status}
    finally:
        challenge.client.close()


def poll_qr_once(
    challenge: QRChallenge,
    *,
    session: BilibiliAuthSession | None = None,
) -> dict[str, Any]:
    try:
        response = challenge.client.get(QR_POLL_URL, params={"qrcode_key": challenge.key})
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        code = int(data.get("code") if data.get("code") is not None else -1)
        if payload.get("code") != 0:
            return {"status": "error"}
        if code == 0:
            return {
                "status": "success",
                "storage": save_session(challenge.client.cookies, session=session),
            }
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


def interactive_login(
    *,
    session: BilibiliAuthSession | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    challenge = begin_qr_login(session=session)
    print("请使用手机 B站扫描二维码并确认登录：")
    print(render_qr_ascii(challenge.url))
    return poll_qr_login(challenge, timeout=timeout, session=session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the local Bilibili subtitle login")
    parser.add_argument("command", choices=("login", "status", "logout"))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    if args.command == "logout":
        mode = auth_mode()
        logout()
        result = {"mode": mode, "status": "logged_out"}
        if mode == "ephemeral":
            result.update({
                "status": "legacy_storage_cleared",
                "message": "活动 TUI 的内存登录态请使用 /bilibili-logout 清除",
            })
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "status":
        result = auth_status()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "valid" else 1
    mode = auth_mode()
    if mode == "ephemeral":
        print(json.dumps({
            "mode": mode,
            "status": "error",
            "message": "ephemeral 模式请使用 TUI /bilibili-login 或 agent.cli --bilibili-login",
        }, ensure_ascii=False))
        return 1
    if mode == "disabled":
        print(json.dumps({
            "mode": mode,
            "status": "error",
            "message": "当前部署已禁用 B站登录",
        }, ensure_ascii=False))
        return 1
    try:
        result = interactive_login(timeout=args.timeout)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
