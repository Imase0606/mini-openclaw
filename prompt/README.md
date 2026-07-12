# Prompt 模块

该目录保留 ChatML 渲染与工具调用解析兼容代码。正式 CLI/TUI 通过 `agent.runtime.build_system_prompt` 组合稳定 system prompt、相关 Memory、Skill catalog 和高置信 Skill 正文。

动态 Todo 放在消息尾部，避免破坏公共前缀。网页、文件、字幕和 OCR 属于不可信数据，即使其中包含“调用工具”或“忽略规则”，也只能被总结，不能提升权限。
