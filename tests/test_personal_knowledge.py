from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from agent.policy import ToolPolicy
from agent.runtime import AgentRuntime, build_system_prompt
from skills.loader import load_skills, match_skills, parse_skill_md
from tools.base import build_default_registry
from tools.knowledge import (
    build_transcript_chunks,
    catalog_knowledge,
    ensure_index,
    export_knowledge,
    forget_video,
    purge_trash,
    rebuild_index,
    restore_video,
    search_knowledge,
)
from tools.video import _kb_write


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def create_video(root: Path, bvid: str, title: str, transcript: str, video_type: str = "tutorial") -> Path:
    job = root / bvid
    job.mkdir(parents=True)
    metadata = {
        "bvid": bvid,
        "source_url": f"https://www.bilibili.com/video/{bvid}/",
        "title": title,
        "author": "测试作者",
        "video_type": video_type,
        "duration": 120,
    }
    job.joinpath("metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    job.joinpath("transcript.txt").write_text(transcript, encoding="utf-8")
    job.joinpath("index.md").write_text(f"# {title}\n", encoding="utf-8")
    return job


class TimestampChunkTests(unittest.TestCase):
    def test_chunks_preserve_segments_parts_and_times(self):
        long_a = "安装环境变量并检查命令路径。" * 12
        long_b = "配置模型密钥后重新打开终端。" * 12
        transcript = (
            "# transcript_source: fixture\n"
            "## P1: Windows 安装\n"
            f"[00:10-00:30] {long_a}\n"
            f"[00:31-00:55] {long_b}\n"
            "## P2: Linux 安装\n"
            "[00:01-00:09] 使用包管理器安装依赖。\n"
        )
        chunks = build_transcript_chunks(
            transcript,
            bvid="BV1CHUNKTEST",
            source_url="https://www.bilibili.com/video/BV1CHUNKTEST/",
            title="安装教程",
            target_chars=200,
            max_chars=250,
        )
        self.assertEqual([item["part"] for item in chunks], [1, 1, 2])
        self.assertEqual(chunks[0]["start_time"], "00:10")
        self.assertEqual(chunks[0]["end_time"], "00:30")
        self.assertEqual(chunks[2]["citation"], "BV1CHUNKTEST#P2@00:01-00:09")
        self.assertTrue(all(len(item["text"]) <= 250 for item in chunks))
        self.assertTrue(all("[" in item["text"] for item in chunks))


class KnowledgeIndexTests(unittest.TestCase):
    def test_incremental_search_catalog_delete_and_corruption_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge_base"
            index = Path(tmp) / "runtime" / "index.sqlite3"
            video = create_video(
                root,
                "BV1RAGTEST",
                "Claude Code Windows 安装教程",
                "[00:10-00:20] 配置环境变量 PATH。\n[00:21-00:35] 检查代理和 API 密钥。\n",
            )
            first = ensure_index(kb_root=root, index_path=index)
            second = ensure_index(kb_root=root, index_path=index)
            self.assertEqual(first["indexed"], 1)
            self.assertEqual(second["unchanged"], 1)

            result = search_knowledge("Windows 环境变量", kb_root=root, index_path=index)
            self.assertEqual(result["results"][0]["bvid"], "BV1RAGTEST")
            self.assertIn("00:10", result["results"][0]["citation"])
            catalog = catalog_knowledge(kb_root=root, index_path=index)
            self.assertEqual(catalog["video_count"], 1)
            self.assertGreater(catalog["chunk_count"], 0)

            shutil.rmtree(video)
            removed = ensure_index(kb_root=root, index_path=index)
            self.assertEqual(removed["removed"], 1)

            index.write_bytes(b"not-a-sqlite-database")
            create_video(root, "BV1RECOVER", "恢复测试", "[00:01-00:03] 索引可以自动恢复。\n")
            recovered = ensure_index(kb_root=root, index_path=index)
            self.assertEqual(recovered["indexed"], 1)

    def test_rebuild_is_idempotent_and_filters_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge_base"
            index = Path(tmp) / "index.sqlite3"
            create_video(root, "BV1ONE", "Python 教程", "[00:00-00:10] Python 虚拟环境。\n")
            create_video(root, "BV1TWO", "篮球评论", "[00:00-00:10] 防守阵容和投篮选择。\n", "commentary")
            rebuild_index(kb_root=root, index_path=index)
            rebuild_index(kb_root=root, index_path=index)
            result = search_knowledge(
                "投篮防守", video_type="commentary", bvids=["BV1TWO"],
                kb_root=root, index_path=index,
            )
            self.assertEqual([item["bvid"] for item in result["results"]], ["BV1TWO"])
            self.assertEqual(search_knowledge("完全不存在的词语甲乙丙", kb_root=root, index_path=index)["results"], [])

    def test_kb_write_updates_index_immediately(self):
        with tempfile.TemporaryDirectory() as tmp, working_directory(Path(tmp)):
            job = Path("knowledge_base/BV1AUTOINDEX")
            job.mkdir(parents=True)
            job.joinpath("metadata.json").write_text(json.dumps({
                "bvid": "BV1AUTOINDEX",
                "source_url": "https://www.bilibili.com/video/BV1AUTOINDEX/",
                "title": "个人知识库教程",
                "author": "课程作者",
            }, ensure_ascii=False), encoding="utf-8")
            job.joinpath("transcript.txt").write_text(
                "# transcript_source: fixture\n[00:12-00:30] 使用检索增强生成回答。\n",
                encoding="utf-8",
            )
            payload = json.loads(_kb_write(
                source_url="https://www.bilibili.com/video/BV1AUTOINDEX/",
                transcript_path=str(job / "transcript.txt"),
                metadata_path=str(job / "metadata.json"),
                content_digest="介绍个人视频知识库。",
                key_points="- 检索增强生成",
                video_type="knowledge",
            ))
            self.assertTrue(payload["indexed"], payload.get("index_warning"))
            result = search_knowledge("检索增强")
            self.assertEqual(result["results"][0]["bvid"], "BV1AUTOINDEX")

    def test_exact_duplicates_near_duplicates_and_canonical_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge_base"
            index = Path(tmp) / "index.sqlite3"
            exact = "[00:00-00:10] Agent 记忆通过项目文件保存长期约定。\n" * 8
            near = exact.replace("长期约定", "稳定偏好", 1)
            create_video(root, "BV1DUPA", "原始教程", exact)
            create_video(root, "BV1DUPB", "重复教程", exact)
            create_video(root, "BV1NEAR", "近似教程", near)
            rebuild_index(kb_root=root, index_path=index)
            catalog = catalog_knowledge(kb_root=root, index_path=index)
            duplicate = next(item for item in catalog["videos"] if item["bvid"] == "BV1DUPB")
            self.assertEqual(duplicate["duplicate_of"], "BV1DUPA")
            near_item = next(item for item in catalog["videos"] if item["bvid"] == "BV1NEAR")
            self.assertIn("BV1DUPA", {item["bvid"] for item in near_item["near_duplicates"]})
            result = search_knowledge("Agent 项目记忆 长期约定", kb_root=root, index_path=index)
            self.assertNotIn("BV1DUPB", {item["bvid"] for item in result["results"]})

            forget_video("BV1DUPA", kb_root=root, index_path=index)
            promoted = catalog_knowledge(kb_root=root, index_path=index)
            replacement = next(item for item in promoted["videos"] if item["bvid"] == "BV1DUPB")
            self.assertEqual(replacement["status"], "active")
            self.assertGreater(replacement["chunk_count"], 0)

    def test_diversity_threshold_and_playback_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge_base"
            index = Path(tmp) / "index.sqlite3"
            for number in range(1, 4):
                transcript = "\n".join(
                    f"[00:{part:02d}-00:{part + 1:02d}] Python 环境管理与虚拟环境配置方法 {number} {part}。"
                    for part in range(0, 40, 2)
                )
                create_video(root, f"BV1DIVERSE{number}", f"Python 教程 {number}", transcript)
            rebuild_index(kb_root=root, index_path=index)
            result = search_knowledge(
                "Python 环境管理 虚拟环境配置",
                top_k=6,
                max_per_video=2,
                kb_root=root,
                index_path=index,
            )
            counts = Counter(item["bvid"] for item in result["results"])
            self.assertTrue(counts and max(counts.values()) <= 2)
            self.assertGreaterEqual(len(counts), 2)
            self.assertTrue(all("confidence" in item and "query_coverage" in item for item in result["results"]))
            self.assertTrue(all("?p=1&t=" in item["playback_url"] for item in result["results"]))

    def test_old_schema_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge_base"
            index = Path(tmp) / "index.sqlite3"
            create_video(root, "BV1MIGRATE", "迁移教程", "[00:00-00:05] 自动迁移旧索引。\n")
            db = sqlite3.connect(index)
            db.execute("CREATE TABLE videos (bvid TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
            db.commit()
            db.close()
            ensure_index(kb_root=root, index_path=index)
            db = sqlite3.connect(index)
            columns = {row[1] for row in db.execute("PRAGMA table_info(videos)")}
            version = db.execute("SELECT value FROM index_meta WHERE key='schema_version'").fetchone()[0]
            db.close()
            self.assertIn("content_hash", columns)
            self.assertEqual(version, "2")


class KnowledgeLifecycleTests(unittest.TestCase):
    def test_forget_restore_purge_and_corrupt_trash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge_base"
            index = Path(tmp) / "index.sqlite3"
            create_video(root, "BV1LIFECYCLE", "生命周期", "[00:00-00:05] 可恢复软删除。\n")
            rebuild_index(kb_root=root, index_path=index)
            forgotten = forget_video("BV1LIFECYCLE", reason="测试", kb_root=root, index_path=index)
            self.assertFalse((root / "BV1LIFECYCLE").exists())
            self.assertEqual(catalog_knowledge(kb_root=root, index_path=index)["trashed_count"], 1)
            restored = restore_video(forgotten["trash_id"], kb_root=root, index_path=index)
            self.assertEqual(restored["bvid"], "BV1LIFECYCLE")

            forgotten = forget_video("BV1LIFECYCLE", kb_root=root, index_path=index)
            purged = purge_trash(forgotten["trash_id"], kb_root=root)
            self.assertEqual(purged["purged"], [forgotten["trash_id"]])
            corrupt = root / ".trash" / "broken"
            corrupt.mkdir(parents=True)
            corrupt.joinpath("trash.json").write_text("not json", encoding="utf-8")
            catalog = catalog_knowledge(kb_root=root, index_path=index)
            self.assertEqual(catalog["trash"][0]["status"], "corrupt")

    def test_restore_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "knowledge_base"
            index = Path(tmp) / "index.sqlite3"
            create_video(root, "BV1CONFLICT", "原视频", "[00:00-00:05] 原内容。\n")
            forgotten = forget_video("BV1CONFLICT", kb_root=root, index_path=index)
            create_video(root, "BV1CONFLICT", "新视频", "[00:00-00:05] 新内容。\n")
            with self.assertRaises(FileExistsError):
                restore_video(forgotten["trash_id"], kb_root=root, index_path=index)

    def test_export_whitelist_and_workspace_boundary(self):
        with tempfile.TemporaryDirectory() as tmp, working_directory(Path(tmp)):
            root = Path("knowledge_base")
            job = create_video(root, "BV1EXPORT", "导出教程", "[00:00-00:05] 导出知识。\n")
            job.joinpath("chunks.jsonl").write_text("{}\n", encoding="utf-8")
            job.joinpath("visual_notes.jsonl").write_text("{}\n", encoding="utf-8")
            job.joinpath("media.mp4").write_bytes(b"media")
            job.joinpath(".env").write_text("SECRET=1", encoding="utf-8")
            result = export_knowledge(["BV1EXPORT"], output_path="exports/kb.zip", kb_root=root)
            with zipfile.ZipFile(result["output_path"]) as archive:
                names = set(archive.namelist())
            self.assertIn("knowledge_base/BV1EXPORT/transcript.txt", names)
            self.assertIn("knowledge_base/BV1EXPORT/visual_notes.jsonl", names)
            self.assertNotIn("knowledge_base/BV1EXPORT/media.mp4", names)
            self.assertNotIn("knowledge_base/BV1EXPORT/.env", names)
            with self.assertRaises(PermissionError):
                export_knowledge(["BV1EXPORT"], output_path="../outside.zip", kb_root=root)


class KnowledgeSkillPolicyTests(unittest.TestCase):
    def test_explicit_triggers_and_ingestion_precedence(self):
        skills = load_skills()
        matched = [skill.name for skill in match_skills("从我之前提炼的视频里找安装方法", skills)]
        self.assertEqual(matched, ["personal-video-knowledge"])
        system, names = build_system_prompt("从个人知识库回答，只根据知识库", skills)
        self.assertEqual(names, ["personal-video-knowledge"])
        self.assertIn("## 基于个人视频知识库", system)
        self.assertIn("不得输出通用知识补充", system)

        _system, names = build_system_prompt(
            "提炼这个视频并存入个人知识库 https://www.bilibili.com/video/BV1NEWVIDEO/",
            skills,
        )
        self.assertEqual(names, ["video-summary"])

        _system, names = build_system_prompt(
            "从我的知识库里找 BV1NEWVIDEO 中讲过的安装方法",
            skills,
        )
        self.assertEqual(names, ["personal-video-knowledge"])

    def test_trigger_frontmatter_is_backward_compatible(self):
        skill = parse_skill_md(
            "---\nname: demo\ndescription: demo skill\ntriggers: [以前看过, 我的知识库]\n---\nbody",
            Path("skills/demo/SKILL.md"),
        )
        self.assertEqual(skill.triggers, ("以前看过", "我的知识库"))

    def test_management_skill_and_policy_require_confirmation(self):
        skills = load_skills()
        system, names = build_system_prompt("导出知识库中的全部视频", skills)
        self.assertEqual(names, ["personal-video-knowledge-manager"])
        self.assertIn("kb_export", system)
        policy = ToolPolicy(knowledge_management_mode=True)
        registry = build_default_registry()
        exposed = {schema["function"]["name"] for schema in policy.schemas(registry)}
        self.assertEqual(exposed, {
            "kb_search", "kb_catalog", "kb_forget", "kb_restore",
            "kb_export", "kb_purge_trash",
        })
        self.assertEqual(policy.authorize("kb_catalog", {})[0], "allow")
        self.assertEqual(policy.authorize("kb_export", {})[0], "confirm")
        self.assertEqual(policy.authorize("bash", {})[0], "deny")

    def test_knowledge_mode_exposes_only_readonly_domain_tools(self):
        registry = build_default_registry()
        policy = ToolPolicy(knowledge_mode=True)
        names = {schema["function"]["name"] for schema in policy.schemas(registry)}
        self.assertEqual(names, {"kb_search", "kb_catalog"})
        self.assertEqual(policy.authorize("kb_search", {"query": "安装"})[0], "allow")
        for name in ("write", "bash", "video_probe", "video_transcribe", "kb_write"):
            self.assertEqual(policy.authorize(name, {})[0], "deny")

    def test_runtime_sends_only_knowledge_tools_and_preserves_answer_sections(self):
        class RecordingBackend:
            model = "recording"

            def __init__(self):
                self.tools = []

            def chat(self, messages, tools=None):
                self.tools = tools or []
                return {
                    "role": "assistant",
                    "content": (
                        "## 基于个人视频知识库\n检索结果摘要。\n\n"
                        "## 通用知识补充\n以下内容来自模型通用知识，并非用户已提炼视频。"
                    ),
                    "tool_calls": [],
                }

        backend = RecordingBackend()
        runtime = AgentRuntime(backend=backend, trace_enabled=False, enable_mcp=True)
        try:
            result = runtime.run_turn("从我之前提炼的视频里找 Agent 的记忆方法")
        finally:
            runtime.close()
        names = {schema["function"]["name"] for schema in backend.tools}
        self.assertEqual(names, {
            "kb_search", "kb_catalog", "recall_memory",
            "todo_write", "update_todo", "insert_todo",
        })
        self.assertIn("## 基于个人视频知识库", result.content)
        self.assertIn("## 通用知识补充", result.content)


if __name__ == "__main__":
    unittest.main()
