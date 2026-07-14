# TUI

![像素知识终端](assets/knowledge-terminal-128.png)

`mini-openclaw` 是基于 Textual 的正式交互入口，与 CLI 共用 `agent.runtime.AgentRuntime`。`MainScreen` 只维护界面状态；`AgentWorker` 在线程中调用共享 runtime，并把 `AgentEvent` 投递回 Textual 主循环。

会话会脱敏保存到 `.mini-openclaw/sessions/`，通过 `/resume` 手动恢复。权限支持 `default`、`acceptEdits` 和 `plan`，任何模式都不能覆盖 `deny`。图片通过 `/image <path>` 排队，知识库产物通过 `/open <n>` 在 TUI 内预览；Markdown 使用原生渲染，图片使用 ANSI 真彩色预览，视觉笔记支持逐帧切换。已完成的回答可通过消息下方的 `Copy` 按钮复制原始 Markdown，`/copy` 可复制最近一条回答。

## 交互

- `/new`、`/sessions`、`/resume` 管理会话；`/compact` 手动压缩长上下文。
- `/model` 切换环境变量已配置的模型；`/permissions` 设置权限模式。
- `/copy` 复制最近一条已完成回答的原始 Markdown。
- 输入 `/` 或 `@` 获取命令和工作区文件补全；输入 `!command` 直接调用受保护的 Shell。
- `Ctrl+C` 中断，`Ctrl+D` 退出，`Shift+Tab` 循环权限模式，`Ctrl+B` 展开详情抽屉。
- 运行中仍可提交请求，最多保留 10 条 FIFO 队列。窄终端会把详情抽屉放到对话区下方。

`acceptEdits` 只自动批准工作区内普通 `write/edit`；`plan` 隐藏并拒绝修改、Shell 和知识库写入。路径保护、敏感文件拒绝和 bubblewrap 在所有模式下始终有效。

工具结果、路径、会话标题和用户文本必须以纯文本或结构化 `Text` 渲染，不能直接作为 Textual markup。每次 AgentWorker 都以 `worker_finished` 完成生命周期交接；TUI 在收到该事件前保持 busy，渲染异常时取消当前任务并清空队列，防止同一 Runtime 并发执行。

运行 `python -m unittest tests.test_tui tests.test_claude_tui -v` 执行流式、多轮、权限、会话、补全、队列、图片和响应式布局测试。

视觉回归使用 `python -m eval.tui_snapshots`，在 `.mini-openclaw/` 生成 120、80 和 60 列 SVG。欢迎页宽屏采用双栏，窄屏隐藏最近会话，极窄屏只保留品牌、模型、路径和权限状态。
