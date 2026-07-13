# Tools 模块

工具按职责分为文件、Shell、网页、记忆、规划、视频提取和个人知识检索。所有模型调用先经过 `ToolPolicy`；文件路径限制在工作区，Shell 使用 bubblewrap，网页使用出站白名单。

视频工具只处理公开 B站内容，临时媒体处理后不长期保存完整音视频。用户可通过 `python -m tools.bilibili_auth login` 扫码登录以读取公开视频的内置字幕；登录态保存在用户主目录且不注册为 Agent Tool。字幕不可用时，ASR 必须显式确认。

`kb_search` 使用中文字符二元组、ASCII 词、BM25 类评分和轻量 MMR 检索历次视频片段，并限制单视频占用；`kb_catalog` 查看 active、duplicate、near-duplicate 和 trashed 状态。派生 SQLite 索引位于 `.mini-openclaw/video_knowledge.sqlite3`，schema 不兼容或损坏时自动重建。

`kb_forget`、`kb_restore`、`kb_export` 和 `kb_purge_trash` 管理知识资产生命周期，均需确认。维护入口为 `python -m tools.knowledge list|search|reindex|forget|restore|export|purge`。
