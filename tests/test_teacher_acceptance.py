from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from eval.teacher_acceptance import (
    discover_b1_candidates,
    discover_b2_candidates,
    main as acceptance_main,
    run_authenticated_subtitle_live,
    run_b3_cases,
    run_live,
    run_offline,
)
from tools.bilibili_subtitles import SubtitleResult


class TeacherAcceptanceTests(unittest.TestCase):
    CANDIDATE = {
        "bvid": "BV1FRESHLIVE",
        "cid": 123,
        "title": "现场候选",
        "video_type": "科学科普",
        "duration": 120,
        "url": "https://www.bilibili.com/video/BV1FRESHLIVE/",
        "discovery_source": "newlist:rid=36",
    }

    def test_offline_teacher_matrix(self):
        results = run_offline()
        self.assertTrue(all(item["passed"] for item in results), results)
        self.assertEqual([item["name"] for item in results], [
            "B1 字幕优先",
            "B2 ASR 降级",
            "B3 无内容不编造",
            "B4 外部指令隔离",
            "B5 OCR 受限后备",
        ])

    def test_fresh_live_logs_in_with_process_scoped_ephemeral_session(self):
        observed = []

        def fake_login(*, session, timeout=180):
            observed.append((session.mode, timeout))
            cookies = httpx.Cookies()
            cookies.set("SESSDATA", "teacher-secret", domain=".bilibili.com", path="/")
            session.save(cookies)
            return {"status": "success", "storage": "ephemeral"}

        live_result = {"name": "fresh", "passed": True, "evidence": "ephemeral"}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"BILIBILI_AUTH_MODE": "ephemeral"}
        ), patch("tools.bilibili_auth.auth_root", return_value=Path(tmp)), patch(
            "tools.bilibili_auth._keyring", return_value=None
        ), patch("eval.teacher_acceptance.interactive_login", side_effect=fake_login), patch(
            "eval.teacher_acceptance.run_fresh_live", return_value=[live_result]
        ):
            code = acceptance_main(["--fresh-live", "--yes-asr", "--artifacts-dir", tmp])
            report = (Path(tmp) / "report.json").read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(observed, [("ephemeral", 180)])
        self.assertNotIn("teacher-secret", report)

    def test_live_acceptance_returns_a_result(self):
        def fake_transcribe(*_args, **kwargs):
            if not kwargs.get("allow_asr"):
                return json.dumps({
                    "ok": False,
                    "status": "asr_confirmation_required",
                    "requires_confirmation": True,
                    "source": "",
                })
            job = Path("knowledge_base/BV1LIVEFIXTURE")
            job.mkdir(parents=True)
            transcript = job / "transcript.txt"
            transcript.write_text(
                "[00:00-00:08] 真实验收会读取这一段证据。\n"
                "[00:08-00:16] 第二段用于满足内容可靠性阈值。\n",
                encoding="utf-8",
            )
            metadata = job / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            return json.dumps({
                "ok": True,
                "usable_content": True,
                "segments": 2,
                "source": "asr",
                "content_status": "sufficient",
                "transcript_path": str(transcript),
                "metadata_path": str(metadata),
            })

        def fake_kb_write(**_kwargs):
            markdown = Path("knowledge_base/BV1LIVEFIXTURE/index.md")
            markdown.write_text("# fixture\n", encoding="utf-8")
            chunks = Path("knowledge_base/BV1LIVEFIXTURE/chunks.jsonl")
            chunks.write_text('{"text":"fixture"}\n', encoding="utf-8")
            return json.dumps({
                "indexed": True,
                "chunks": 1,
                "markdown_path": str(markdown),
                "chunks_path": str(chunks),
            })

        with patch("eval.teacher_acceptance._transcribe", side_effect=fake_transcribe), patch(
            "eval.teacher_acceptance._kb_write", side_effect=fake_kb_write
        ):
            result = run_live("BV1LIVEFIXTURE")

        self.assertTrue(result["passed"], result)
        self.assertEqual(result["name"], "LIVE 真实 B站 ASR 与落库")

    def test_live_asr_rejects_a_subtitle_false_positive(self):
        responses = [
            {
                "ok": False,
                "status": "asr_confirmation_required",
                "requires_confirmation": True,
                "source": "",
            },
            {
                "ok": True,
                "status": "completed",
                "source": "subtitle:bilibili:authenticated:ai-zh",
                "subtitle_status": "authenticated_found",
                "usable_content": True,
                "segments": 20,
            },
        ]
        with patch("eval.teacher_acceptance._transcribe", side_effect=[json.dumps(item) for item in responses]):
            result = run_live("BV1FALSEASR")
        self.assertFalse(result["passed"])
        self.assertIn("source=subtitle:bilibili:authenticated", result["evidence"])

    def test_authenticated_subtitle_live_writes_kb_without_asr(self):
        def fake_transcribe(*_args, **_kwargs):
            job = Path("knowledge_base/BV1AUTHTEACHER")
            job.mkdir(parents=True)
            transcript = job / "transcript.txt"
            transcript.write_text(
                "[00:00-00:08] 登录字幕提供第一段知识内容。\n"
                "[00:08-00:16] 第二段用于生成可检索切片。\n",
                encoding="utf-8",
            )
            metadata = job / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            return json.dumps({
                "ok": True,
                "source": "subtitle:bilibili:authenticated:ai-zh",
                "subtitle_status": "authenticated_found",
                "auth_status": "valid",
                "auth_used": True,
                "usable_content": True,
                "segments": 2,
                "transcript_path": str(transcript),
                "metadata_path": str(metadata),
            })

        def fake_kb_write(**_kwargs):
            job = Path("knowledge_base/BV1AUTHTEACHER")
            markdown = job / "index.md"
            chunks = job / "chunks.jsonl"
            markdown.write_text("# fixture\n", encoding="utf-8")
            chunks.write_text('{"text":"fixture"}\n', encoding="utf-8")
            return json.dumps({
                "indexed": True,
                "chunks": 1,
                "markdown_path": str(markdown),
                "chunks_path": str(chunks),
            })

        with patch(
            "eval.teacher_acceptance.auth_status",
            return_value={"mode": "ephemeral", "status": "valid", "expires_in_seconds": 1200},
        ), patch(
            "eval.teacher_acceptance._transcribe", side_effect=fake_transcribe
        ), patch("eval.teacher_acceptance._kb_write", side_effect=fake_kb_write):
            result = run_authenticated_subtitle_live("BV1AUTHTEACHER")

        self.assertTrue(result["passed"], result)
        self.assertEqual(result["asr_calls"], 0)
        self.assertEqual(result["chunks"], 1)
        self.assertEqual(result["auth_mode"], "ephemeral")

    def test_b1_discovery_requires_complete_authenticated_subtitle(self):
        subtitle = SubtitleResult(
            status="authenticated_found",
            language="ai-zh",
            segments=[{"text": "这是足够长的知识字幕内容" * 2} for _ in range(12)],
            auth_status="valid",
            auth_used=True,
        )
        with patch("eval.teacher_acceptance.auth_status", return_value={"status": "valid"}), patch(
            "eval.teacher_acceptance._b1_pool", return_value=[dict(self.CANDIDATE)]
        ), patch("eval.teacher_acceptance.fetch_subtitles", return_value=subtitle):
            candidates = discover_b1_candidates()
        self.assertEqual([item["bvid"] for item in candidates], ["BV1FRESHLIVE"])
        self.assertEqual(candidates[0]["subtitle_segments"], 12)

    def test_b2_discovery_rejects_an_intermittent_subtitle(self):
        missing = SubtitleResult(
            status="error",
            auth_status="valid",
            auth_used=True,
            fallback_reason="authenticated_subtitle_incomplete",
        )
        found = SubtitleResult(
            status="authenticated_found",
            segments=[{"text": "字幕恢复"}],
            auth_status="valid",
            auth_used=True,
        )
        candidate = {**self.CANDIDATE, "video_type": "日常"}
        with patch("eval.teacher_acceptance._b2_pool", return_value=[candidate]), patch(
            "eval.teacher_acceptance.fetch_subtitles", side_effect=[missing, found]
        ), patch("eval.teacher_acceptance._transcribe") as transcribe:
            candidates = discover_b2_candidates(audit_delay=0)
        self.assertEqual(candidates, [])
        transcribe.assert_not_called()

    def test_b2_discovery_rejects_api_errors(self):
        failed = SubtitleResult(
            status="error",
            auth_status="valid",
            auth_used=True,
            fallback_reason="authenticated_subtitle_error:TimeoutError",
        )
        candidate = {**self.CANDIDATE, "video_type": "日常"}
        with patch("eval.teacher_acceptance._b2_pool", return_value=[candidate]), patch(
            "eval.teacher_acceptance.fetch_subtitles", return_value=failed
        ), patch("eval.teacher_acceptance._transcribe") as transcribe:
            candidates = discover_b2_candidates(audit_delay=0)
        self.assertEqual(candidates, [])
        transcribe.assert_not_called()

    def test_b2_discovery_requires_double_miss_and_full_preflight(self):
        missing = SubtitleResult(
            status="not_found",
            auth_status="valid",
            auth_used=True,
            fallback_reason="authenticated_subtitle_not_found",
        )
        candidate = {**self.CANDIDATE, "video_type": "日常"}
        preflight = json.dumps({
            "status": "asr_confirmation_required",
            "requires_confirmation": True,
            "fallback_reason": "authenticated_subtitle_not_found",
        })
        with patch("eval.teacher_acceptance._b2_pool", return_value=[candidate]), patch(
            "eval.teacher_acceptance.fetch_subtitles", side_effect=[missing, missing]
        ), patch("eval.teacher_acceptance._transcribe", return_value=preflight):
            candidates = discover_b2_candidates(audit_delay=0)
        self.assertEqual([item["bvid"] for item in candidates], ["BV1FRESHLIVE"])
        self.assertEqual(len(candidates[0]["subtitle_audits"]), 2)

    def test_b2_discovery_marks_duration_rejected_subtitles_as_volatile(self):
        incomplete = SubtitleResult(
            status="error",
            auth_status="valid",
            auth_used=True,
            fallback_reason="authenticated_subtitle_incomplete",
        )
        candidate = {**self.CANDIDATE, "video_type": "日常"}
        preflight = json.dumps({
            "status": "asr_confirmation_required",
            "requires_confirmation": True,
        })
        with patch("eval.teacher_acceptance._b2_pool", return_value=[candidate]), patch(
            "eval.teacher_acceptance.fetch_subtitles", return_value=incomplete
        ), patch("eval.teacher_acceptance._transcribe", return_value=preflight):
            candidates = discover_b2_candidates(audit_delay=0)
        self.assertTrue(candidates[0]["volatile_candidate"])

    def test_b3_persists_three_diagnostic_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_b3_cases(Path(tmp) / "artifacts")
            self.assertTrue(result["passed"], result)
            fixtures = result["fixtures"]
            self.assertEqual([item["fixture"] for item in fixtures], ["empty", "short", "repeated"])
            for item in fixtures:
                self.assertTrue(Path(item["markdown_path"]).is_file())
                self.assertEqual(Path(item["chunks_path"]).read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
