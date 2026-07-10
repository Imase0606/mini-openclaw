---
name: video-summary
description: 当用户提供 B站 Bilibili b23.tv BV 视频链接、字幕或文字稿，并要求视频总结、提炼、Markdown 学习笔记、知识库或 RAG 素材时使用。
---

# B站视频总结与知识库生成

面向无需登录即可访问的 B站公开视频，提取可验证内容并生成给人阅读的 Markdown 学习笔记与 RAG 切块。

## 工作流

1. 调用 `video_probe` 获取标题、UP主、简介、发布时间、时长、BV 号和分 P 信息。
2. 调用一次 `video_transcribe`：
   - 优先使用字幕；没有字幕时使用 faster-whisper 本地 ASR。
   - 多分 P 视频会自动保存 `transcript_pN.txt` 并合并为 `transcript.txt`。不要自行拼接 `?p=N` 重复调用或移动分稿。
   - 默认复用已有完整转写；只有用户明确要求重新提取时才传 `force=true`。
   - 成功后调用 `read` 完整读取返回的 `transcript_path`，不要只根据工具返回的 excerpt 总结。
   - 若部分分 P 失败，只总结成功内容并在信息缺口中列出失败分 P；若全部失败，停止正文总结。
3. 仅当视频包含 PPT、代码、图表、界面操作，或用户明确需要视觉信息时，调用 `video_frame_ocr`。成功后读取 `visual_notes_path`。OCR 只补充画面信息，不替代语音主干。
4. 根据 transcript、OCR 和 metadata 提炼内容，调用 `kb_write`：
   - 传入 `source_url`、`transcript_path`、`metadata_path`，有 OCR 时再传 `visual_notes_path`。
   - 填写 `content_digest`、`key_points`、`section_notes`。
   - 仅在视频确有教程、方法论或可执行步骤时填写 `action_suggestions`。
5. 返回 `knowledge_base/<BV>/index.md` 及相关文件路径，并说明内容依据、缺失信息和可信度。

## 内容要求

- 使用简体中文。字幕、ASR、OCR 和 Markdown 统一经过 OpenCC；工具返回归一化警告时，在可信度说明中注明。
- `内容提要` 在信息充足时写 1-3 个自然段，通常 150-400 字，覆盖背景、主要观点、论证脉络、结论价值和适用场景。
- `核心要点` 必须可追溯到 transcript、OCR 或 metadata；合并重复内容，保留概念、因果关系、步骤、示例和易错点。
- 有时间戳时按时间整理；没有可靠时间戳时改为按主题整理，不得伪造。
- `画面补充信息` 和 `行动建议/学习建议` 只在确有内容时出现，不输出空占位。
- 搜索引擎内容只能标为背景补充，不能冒充视频内容。

## 输出文件

同一视频的文件统一保存在 `knowledge_base/<BV>/`：

- `index.md`：人类可读的知识库正文
- `metadata.json`：视频元数据
- `transcript.txt`：单集转写或多分 P 合并稿
- `transcript_pN.txt`：多分 P 的独立转写
- `visual_notes.jsonl`：OCR 结果（如有）
- `chunks.jsonl`：RAG-ready 切块

`index.md` 使用以下结构：

```markdown
# 标题

## 来源信息
## 来源与文件
## 内容提要
## 核心要点
## 按时间/段落整理
## 画面补充信息（可选）
## 行动建议/学习建议（可选）
## 信息缺口与可信度说明
```

## 忠实性与平台边界

- 只基于工具返回、用户提供内容、明确可见网页内容或本地文件总结。
- 无字幕、无转写、无法访问视频时，不得编造内容或声称已观看完整视频。
- 推测内容使用“可能”“推测”“待确认”，并与事实分开。
- 保留来源 URL、标题层级、分 P、时间戳或来源段落，方便检索和溯源。
- 不绕过登录、会员、付费、私密、地区或反爬限制。
- 不长期保存完整音视频，只保留必要文本、元数据、关键帧摘要和知识库文件。
