from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.teacher_acceptance import run_live, run_offline


class TeacherAcceptanceTests(unittest.TestCase):
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

    def test_live_acceptance_returns_a_result(self):
        def fake_transcribe(*_args, **_kwargs):
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
            return json.dumps({"indexed": True, "chunks": 1, "markdown_path": str(markdown)})

        with patch("eval.teacher_acceptance._transcribe", side_effect=fake_transcribe), patch(
            "eval.teacher_acceptance._kb_write", side_effect=fake_kb_write
        ):
            result = run_live("BV1LIVEFIXTURE")

        self.assertTrue(result["passed"], result)
        self.assertEqual(result["name"], "LIVE 真实 B站 ASR 与落库")


if __name__ == "__main__":
    unittest.main()
