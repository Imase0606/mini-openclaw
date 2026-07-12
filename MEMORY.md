# 项目记忆

## 技术栈
- Python 3.11，主要运行环境为 WSL Ubuntu-22.04 的 Conda `openclaw` 环境。
- LLM 使用 OpenAI-compatible API；普通任务使用 DeepSeek，图片任务可使用 MiMo 视觉模型。
- B站公开视频通过 yt-dlp、faster-whisper、EasyOCR 和 OpenCC 处理。

## 视频知识库约定
- 同一视频产物统一保存到 `knowledge_base/<BV>/`，主文档为 `index.md`。
- 中文 transcript、OCR 和 Markdown 尽量转换为简体中文。
- 总结必须基于 metadata、transcript 和 OCR，不得把搜索结果冒充视频内容。
- 根据视频内容选择 tutorial、knowledge、narrative、commentary 或 general 模板。

## 常用命令
- 自检：`python -m agent.cli --selfcheck`
- 测试：`python -m unittest discover -s tests -v`
- 红队：`python -m security.redteam`

## 已知边界
- 第一版只处理无需登录的 B站公开视频，不绕过会员、登录、私密或地区限制。
- CLI 与 Textual TUI 共用 `AgentRuntime`；TUI 已接入记忆、规划、权限、Skill、MCP、视频策略和 trace。
- TUI 会话仅保存脱敏文字和工作区内产物路径；图片 Base64、密钥和完整运行媒体不持久化。
