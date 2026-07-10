---
name: short-video-kb
description: 当用户提供 B站、Bilibili、b23.tv 或 BV 公热视频链接，并要求视频提炼、总结、生成 Markdown 学习笔记、知识库或 RAG 素材时使用。
---

# B站视频知识库提炼

## 工作流

1. 调用 `video_probe` 获取标题、UP主、简介、时长、发布时间和 BV 号。仅处理无需登录即可访问的公开视频。
2. 调用一次 `video_transcribe`。它会自动处理全部分 P、保存 `transcript_pN.txt` 并合并 `transcript.txt`；不要自行拼接 `?p=N` 重复调用或移动转写文件。优先使用字幕；没有字幕时使用本地 ASR。成功后调用 `read` 完整读取返回的 `transcript_path`。若仅部分分 P 成功，只总结成功内容并列出缺失分 P；若全部失败，停止基于视频正文的总结，不得用标题或搜索结果补写内容。
3. 仅当视频包含 PPT、代码、图表、界面操作，或用户明确需要视觉信息时，调用 `video_frame_ocr`。成功后读取返回的 `visual_notes_path`；OCR 只补充画面信息，不替代转写主干。
4. 根据 transcript、OCR 和 metadata 整理内容，再调用 `kb_write`。优先传入工具生成的 `transcript_path`、`visual_notes_path` 和 `metadata_path`，并提供 `content_digest`、`key_points`、`section_notes`；仅在视频确有教程或方法论时提供 `action_suggestions`。
5. 向用户返回 `knowledge_base/<BV>/index.md` 及相关文件路径，并简要说明内容依据、缺失信息和可信度。

## 内容要求

- 使用简体中文。若工具提示未执行繁简转换，在结果中明确说明。
- 将 `index.md` 写成给人阅读的视频学习笔记；信息充足时，`内容提要` 通常写 150-400 字，覆盖主题背景、主要观点、论证脉络、结论价值和适用场景。
- 保留可用时间戳和来源 URL。核心要点必须能追溯到 transcript、OCR 或 metadata。
- 搜索引擎内容只能标为背景补充，不得冒充视频内容。
- 同一视频的 `index.md`、`metadata.json`、`transcript.txt`、`visual_notes.jsonl` 和 `chunks.jsonl` 必须保存在 `knowledge_base/<BV>/`。
- 不绕过登录、会员、付费、私密、地区或反爬限制，不长期保存完整音视频。
