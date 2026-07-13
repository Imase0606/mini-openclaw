from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from tools import bilibili_auth
from tools.bilibili_subtitles import fetch_subtitles


class BilibiliAuthTests(unittest.TestCase):
    def test_session_file_roundtrip_and_logout(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "tools.bilibili_auth.auth_root", return_value=Path(tmp)
        ), patch("tools.bilibili_auth._keyring", return_value=None):
            cookies = httpx.Cookies()
            cookies.set("SESSDATA", "secret-session", domain=".bilibili.com", path="/")
            cookies.set("bili_jct", "csrf", domain=".bilibili.com", path="/")
            self.assertEqual(bilibili_auth.save_session(cookies), "file")
            loaded, storage = bilibili_auth.load_session()
            self.assertEqual(storage, "file")
            self.assertEqual(loaded.get("SESSDATA", domain=".bilibili.com"), "secret-session")
            path = bilibili_auth.session_path()
            self.assertTrue(path.is_file())
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            bilibili_auth.logout()
            self.assertFalse(path.exists())

    def test_qr_login_state_machine_saves_response_cookies(self):
        polls = iter((86101, 86090, 0))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("generate"):
                return httpx.Response(200, json={
                    "code": 0,
                    "data": {"url": "https://example.test/qr", "qrcode_key": "key"},
                })
            code = next(polls)
            headers = {}
            if code == 0:
                headers["set-cookie"] = "SESSDATA=secret; Domain=.bilibili.com; Path=/; Secure"
            return httpx.Response(200, json={"code": 0, "data": {"code": code}}, headers=headers)

        client = httpx.Client(transport=httpx.MockTransport(handler), headers=bilibili_auth.HEADERS)
        challenge = bilibili_auth.begin_qr_login(client=client)
        self.assertEqual(bilibili_auth.poll_qr_once(challenge)["status"], "waiting_scan")
        self.assertEqual(bilibili_auth.poll_qr_once(challenge)["status"], "scanned_waiting_confirmation")
        with patch("tools.bilibili_auth.save_session", return_value="file") as save:
            self.assertEqual(bilibili_auth.poll_qr_once(challenge)["status"], "success")
            save.assert_called_once()
        client.close()

    def test_status_never_returns_cookie_content(self):
        with patch("tools.bilibili_auth.load_session") as load:
            cookies = httpx.Cookies()
            cookies.set("SESSDATA", "never-expose-this", domain=".bilibili.com", path="/")
            load.return_value = (cookies, "file")
            client = httpx.Client(transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"code": 0, "data": {"isLogin": True}})
            ))
            result = bilibili_auth.auth_status(client=client)
            client.close()
        self.assertEqual(result["status"], "valid")
        self.assertEqual(set(result), {"status"})
        self.assertNotIn("never-expose-this", json.dumps(result))

    def test_terminal_qr_is_rendered_without_exposing_session_data(self):
        rendered = bilibili_auth.render_qr_ascii("https://example.test/short-lived-qr")
        self.assertGreater(len(rendered.splitlines()), 10)
        self.assertNotIn("SESSDATA", rendered)


class BilibiliSubtitleTests(unittest.TestCase):
    def test_authenticated_subtitle_follows_anonymous_miss(self):
        def anonymous_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"subtitle": {"subtitles": []}}})

        def authenticated_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x/player/v2":
                return httpx.Response(200, json={"code": 0, "data": {"subtitle": {"subtitles": [{
                    "lan": "zh-CN", "subtitle_url": "https://aisubtitle.test/sub.json"
                }]}}})
            return httpx.Response(200, json={"body": [
                {"from": 0, "to": 5, "content": "登录字幕第一段"},
                {"from": 5, "to": 10, "content": "登录字幕第二段"},
            ]})

        anonymous = httpx.Client(transport=httpx.MockTransport(anonymous_handler))
        authenticated = httpx.Client(transport=httpx.MockTransport(authenticated_handler))
        with patch("tools.bilibili_subtitles.load_session", return_value=(httpx.Cookies(), "file")):
            result = fetch_subtitles(
                "BV1AUTHSUB", 123,
                anonymous_client=anonymous,
                authenticated_client=authenticated,
                auth_state={"status": "valid"},
            )
        anonymous.close()
        authenticated.close()
        self.assertEqual(result.status, "authenticated_found")
        self.assertEqual(result.language, "zh-CN")
        self.assertTrue(result.auth_used)
        self.assertEqual(len(result.segments), 2)

    def test_authenticated_subtitle_retries_an_empty_player_response(self):
        authenticated_calls = {"player": 0}

        def anonymous_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"subtitle": {"subtitles": []}}})

        def authenticated_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x/player/v2":
                authenticated_calls["player"] += 1
                subtitles = [] if authenticated_calls["player"] == 1 else [{
                    "lan": "ai-zh",
                    "subtitle_url": f"https://aisubtitle.test/retry-{authenticated_calls['player']}.json",
                }]
                return httpx.Response(200, json={
                    "code": 0, "data": {"subtitle": {"subtitles": subtitles}},
                })
            body = [
                {"from": 0, "to": 5, "content": "重试后取得第一段登录字幕。"},
                {"from": 5, "to": 10, "content": "重试后取得第二段登录字幕。"},
            ]
            if request.url.path.endswith("retry-3.json"):
                body.append({"from": 10, "to": 15, "content": "选择片段更完整的第三次结果。"})
            return httpx.Response(200, json={"body": body})

        anonymous = httpx.Client(transport=httpx.MockTransport(anonymous_handler))
        authenticated = httpx.Client(transport=httpx.MockTransport(authenticated_handler))
        with patch("tools.bilibili_subtitles.time.sleep"), patch(
            "tools.bilibili_subtitles.load_session", return_value=(httpx.Cookies(), "file")
        ):
            result = fetch_subtitles(
                "BV1RETRYSUB", 123,
                anonymous_client=anonymous,
                authenticated_client=authenticated,
                auth_state={"status": "valid"},
            )
        anonymous.close()
        authenticated.close()

        self.assertEqual(result.status, "authenticated_found")
        self.assertEqual(result.language, "ai-zh")
        self.assertEqual(len(result.segments), 3)
        self.assertEqual(authenticated_calls["player"], 3)

    def test_authenticated_subtitle_rejects_wrong_video_duration(self):
        calls = {"player": 0}

        def anonymous_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"subtitle": {"subtitles": []}}})

        def authenticated_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x/player/v2":
                calls["player"] += 1
                return httpx.Response(200, json={"code": 0, "data": {"subtitle": {"subtitles": [{
                    "lan": "ai-zh",
                    "subtitle_url": f"https://aisubtitle.test/duration-{calls['player']}.json",
                }]}}})
            end = {1: 210, 2: 40}.get(calls["player"], 99)
            return httpx.Response(200, json={"body": [
                {"from": 0, "to": end / 2, "content": "时长校验字幕第一段。"},
                {"from": end / 2, "to": end, "content": "时长校验字幕第二段。"},
            ]})

        anonymous = httpx.Client(transport=httpx.MockTransport(anonymous_handler))
        authenticated = httpx.Client(transport=httpx.MockTransport(authenticated_handler))
        with patch("tools.bilibili_subtitles.time.sleep"), patch(
            "tools.bilibili_subtitles.load_session", return_value=(httpx.Cookies(), "file")
        ):
            result = fetch_subtitles(
                "BV1DURATIONSUB", 123,
                anonymous_client=anonymous,
                authenticated_client=authenticated,
                auth_state={"status": "valid"},
                expected_duration=100,
            )
        anonymous.close()
        authenticated.close()

        self.assertEqual(result.status, "authenticated_found")
        self.assertEqual(result.segments[-1]["end"], 99)
        self.assertEqual(calls["player"], 3)

    def test_no_login_is_distinct_from_no_subtitle(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"code": 0, "data": {"subtitle": {"subtitles": []}}})
        ))
        result = fetch_subtitles(
            "BV1NOSUB", 123,
            anonymous_client=client,
            auth_state={"status": "not_logged_in"},
        )
        client.close()
        self.assertEqual(result.status, "auth_not_configured")
        self.assertEqual(result.auth_status, "not_logged_in")


if __name__ == "__main__":
    unittest.main()
