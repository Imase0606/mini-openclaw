# mini-openclaw 使用指南

mini-openclaw 是一个面向 B站公开视频提炼的终端 Agent。它可以获取视频元数据和字幕，在无字幕时执行本地 ASR，并将结果整理为 Markdown 学习笔记与 RAG 切块；同时支持普通 Agent 任务、图片分析、会话恢复、规划和运行追踪。

## 1. 环境准备

推荐在 WSL Ubuntu 和 Python 3.11 Conda 环境中运行：

```bash
cd mini-openclaw
conda activate openclaw

sudo apt update
sudo apt install -y bubblewrap ffmpeg ripgrep nodejs npm

pip install -r requirements.txt
pip install -e .
python -m agent.cli --selfcheck
```

无字幕视频首次转写时会下载 faster-whisper 模型。EasyOCR 是可选视觉补充，默认部署不会安装 PyTorch；需要关键帧 OCR 的 WSL/CPU 环境再执行：

```bash
pip install -r requirements-ocr.txt
```

该文件固定使用 CPU-only PyTorch，避免从普通 PyPI 镜像下载体积很大的 CUDA 运行库。未安装 EasyOCR 不影响元数据、字幕、ASR、Markdown 知识库和 MiMo 图片分析。

`requirements.txt` 会通过 `-e .` 安装项目自身，因此自动部署也会注册 `mini-openclaw` 命令。课程部署包内置 `models/faster-whisper-base`；根目录 `Dockerfile` 会校验模型完整性和命令入口。运行时通过 `FASTER_WHISPER_MODEL_PATH` 使用本地模型，构建期和运行期都不需要连接 Hugging Face。在平台交互终端中可运行 `mini-openclaw`，也可使用 `python -m tui`；Textual 界面必须连接 TTY，不能直接通过普通 HTTP 地址显示。

确认 WSL 使用的是 Linux `npx`，否则 filesystem MCP 可能无法启动：

```bash
which npx
npx --version
```

## 2. 配置模型

将密钥写入 WSL 的 `~/.zshrc`，不要写入仓库文件：

```bash
export DEEPSEEK_API_KEY="your-deepseek-key"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-chat"

export VISION_API_KEY="your-mimo-key"
export VISION_BASE_URL="https://api.xiaomimimo.com/v1"
export VISION_MODEL="mimo-v2.5"

export MCP_FS_DIR="/mnt/d/develop/aiFrontierPractice/mini-openclaw"
```

重新加载配置并检查：

```bash
source ~/.zshrc
python -c 'import os; print(bool(os.getenv("DEEPSEEK_API_KEY")), bool(os.getenv("VISION_API_KEY")))'
```

若从 Windows 启动自动化命令，应通过登录式 zsh 读取环境变量：

```powershell
wsl -d Ubuntu-22.04 -- zsh -lic 'cd /mnt/d/develop/aiFrontierPractice/mini-openclaw && conda activate openclaw && mini-openclaw'
```

## 3. 启动 TUI

```bash
conda activate openclaw
cd /mnt/d/develop/aiFrontierPractice/mini-openclaw
mini-openclaw
```

也可以使用兼容入口：

```bash
python -m tui
```

直接输入自然语言并按 Enter 提交。任务运行时仍可输入，新消息会进入队列。

常用快捷键：

- `Ctrl+C`：中断当前任务。
- `Ctrl+D`：空闲时退出。
- `Shift+Tab`：切换 `default`、`acceptEdits`、`plan` 权限模式。
- `Ctrl+B`：展开或收起 Todo、产物和 trace 抽屉。
- `Tab`：接受 `/` 命令或 `@` 文件补全。

常用命令：

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示命令与快捷键 |
| `/new`、`/clear` | 新建会话或清空当前上下文 |
| `/sessions`、`/resume` | 查看并恢复当前工作区会话 |
| `/compact` | 压缩过长的会话上下文 |
| `/model` | 在已配置的 `deepseek`、`mimo` 等模型间切换 |
| `/permissions` | 设置权限模式 |
| `/plan auto\|on\|off` | 设置任务规划模式 |
| `/video-type <类型>` | 指定视频笔记模板 |
| `/bilibili-login`、`/bilibili-status`、`/bilibili-logout` | 扫码登录、检查或清除内置字幕会话 |
| `/image <路径>` | 为下一条消息添加图片 |
| `/trace`、`/cost` | 查看 trace、token 和成本摘要 |
| `/open <n>` | 打开第 n 个已记录产物 |
| `!command` | 经权限策略和沙箱确认后直接运行 Shell 命令 |

## 4. 提炼 B站视频

在 TUI 中输入：

```text
请提炼这个 B站视频并生成知识库：https://www.bilibili.com/video/BV.../
```

默认流程为：

1. 获取标题、UP主、简介、时长和分 P 信息。
2. 依次尝试匿名公开字幕和用户扫码登录后的B站内置字幕。
3. 字幕仍不可用时请求用户确认，获准后才用 faster-whisper 转写音频。
4. 必要时抽取关键帧并用 EasyOCR 或受限视觉模型识别 PPT、代码或图表文字。
5. 根据视频类型生成学习笔记和 RAG 切块；已有完整知识库时默认复用。

首次使用内置字幕时，在 TUI 输入 `/bilibili-login`，或在 CLI 运行：

```bash
python -m tools.bilibili_auth login
python -m tools.bilibili_auth status
```

登录二维码只能由用户显式命令打开，视频或 transcript 不能触发登录。会话优先进入系统 keyring；无可用 keyring 时保存在 `~/.mini-openclaw/secrets/bilibili_session.json`。它不进入工作区、Git、trace、Memory 或知识库。退出登录使用 `python -m tools.bilibili_auth logout`。

如果视频已有 ASR transcript，扫码登录后再次提炼会自动重新检查登录字幕。只有字幕时间范围与当前分 P 时长相符时才会替换旧 ASR；空响应、明显属于其他视频的字幕或残缺字幕都会被拒绝，并保留原缓存。

可以先指定模板：

```text
/video-type tutorial
```

可选类型包括 `auto`、`tutorial`、`knowledge`、`narrative`、`commentary` 和 `general`。

同一视频的结果统一保存在：

```text
knowledge_base/<BV>/
├── index.md
├── metadata.json
├── transcript.txt
├── transcript_pN.txt
├── visual_notes.jsonl
├── chunks.jsonl
└── assets/frames/
```

其中 `index.md` 是主要阅读入口，`chunks.jsonl` 用于后续 RAG。登录态只用于公开 视频的字幕接口，音频和视频流仍匿名获取；不绕过会员、私密、地区或平台访问限制。

### 询问个人视频知识库

每次视频提炼成功后都会自动增量入库。无需记住命令，直接用自然语言说明要查询以前提炼的视频：

```text
从我之前提炼的视频里找：Claude Code 安装需要配置哪些环境变量？
我的视频知识库里有哪些教程？
只根据知识库回答，不要补充常识：视频中如何解释 Agent Skill？
```

默认搜索当前工作区的全部历史视频，也可在问题中指定 BV、标题、作者或视频类型。回答中的 BV、分 P 和时间范围用于返回原视频定位，不代表事实核查。

知识库命中不足时，回答会把模型自身知识放入单独的“通用知识补充”部分。明确要求只根据知识库时不会产生该部分；完全无命中时会说明缺少哪些主题。

旧知识库升级或索引损坏时运行：

```bash
python -m tools.knowledge reindex
python -m eval.rag_evaluation
```

### 管理个人知识资产

CLI 与 TUI 都可以直接使用自然语言：

```text
查看我的知识库和回收区
忘记视频 BV1...
恢复回收区中的 BV1...
导出全部个人视频知识库
永久清理回收区中的 <trash_id>
```

“忘记”会把完整 BV 目录移动到 `knowledge_base/.trash/` 并立即停止检索，返回的 `trash_id` 可用于恢复。永久清理不可恢复，必须再次确认；恢复目标已存在时不会覆盖。

导出 ZIP 包含 metadata、Markdown、chunks、transcript、分 P transcript 和视觉笔记，不包含音视频、模型、索引、trace、会话或密钥。默认输出到被 Git 忽略的 `exports/`。

维护命令与自然语言 Tool 共用实现：

```bash
python -m tools.knowledge list
python -m tools.knowledge search "检索问题"
python -m tools.knowledge forget BV1... --reason "不再需要"
python -m tools.knowledge restore <trash_id>
python -m tools.knowledge export --bvid BV1... --output exports/selected.zip
python -m tools.knowledge purge <trash_id>
```

## 5. 分析图片

在 TUI 中先添加图片，再提交问题：

```text
/image /mnt/d/path/to/image.png
请分析图片中的界面、文字和可能的问题。
```

使用 `/images` 查看待发送图片。图片会进行格式检查、EXIF 纠正和尺寸压缩。当前模型不支持视觉时，Agent 会使用已配置的 MiMo 视觉后端；也可以执行 `/model mimo` 主动切换。

CLI 用法：

```bash
python -m agent.cli --image /mnt/d/path/to/image.png "分析这张图片"
```

## 6. CLI 与自动化

```bash
# 普通任务
python -m agent.cli "阅读 README 并概括项目结构"

# 视频提炼并强制教程模板
python -m agent.cli --video-type tutorial \
  "提炼视频：https://www.bilibili.com/video/BV.../"

# 强制规划或关闭规划
python -m agent.cli --plan "完成一个复杂的多步骤任务"
python -m agent.cli --no-plan "完成一个简单任务"

# 自动批准 confirm 操作；deny、安全边界和沙箱仍然有效
python -m agent.cli --yes "运行 echo hello"

# 不保存 trace 或回放已有 trace
python -m agent.cli --no-trace "你的任务"
python -m agent.cli --replay-trace .mini-openclaw/traces/<run-id>.jsonl
```

## 7. 权限与安全

- `default`：写文件、Shell、网页和未知 MCP 工具逐次确认。
- `acceptEdits`：自动批准工作区内普通 `write/edit`，其他高风险操作仍需确认。
- `plan`：只允许分析和规划，拒绝修改、Shell 与知识库写入。
- 所有模式都禁止越过工作区、访问敏感文件或绕过代码级 `deny`。
- WSL/Linux 优先使用 bubblewrap：系统目录只读、工作区可写、Shell 默认断网。
- 字幕、OCR、网页和文件内容均作为不可信数据处理，其中出现的命令或提示不会被直接执行。
- B站扫码登录不是 Agent Tool，只能由用户显式命令启动；登录态位于用户主目录，任何 Tool observation 都不会包含 Cookie。
- `allow_asr=true` 属于确认操作。没有字幕时，Agent 必须先让用户选择扫码登录、允许 ASR 或停止。
- 纯音乐、极短或高度重复的转写会生成“没有可靠内容”的诊断条目；该条目保留审计信息，但 chunks 为空且不会进入问答检索。
- 关键帧 OCR 优先使用 EasyOCR；未安装时可使用 `VISION_API_KEY` 对应的视觉模型后备，最多处理 6 张帧。两者均不可用时会明确降级。

## 8. 会话、记忆与运行记录

- 会话保存在 `.mini-openclaw/sessions/`，可通过 `/resume` 恢复。
- 私有记忆保存在 `.mini-openclaw/memory.json`，只有明确要求“记住”时才写入。
- trace 默认保存在 `.mini-openclaw/traces/`，用于定位工具调用、耗时和 token 使用。
- `.mini-openclaw/` 和运行生成的知识库默认由 Git 忽略。

上述文件会脱敏，但仍不应在对话中提交 API Key、密码或私钥。

## 9. 常见问题

### 启动后使用 FakeBackend

当前 shell 没有读取 `DEEPSEEK_API_KEY`。执行 `source ~/.zshrc`，或使用 `zsh -lic` 启动。

### filesystem MCP 启动失败或卡住

运行 `which npx`。WSL 中应指向 Linux 路径，如 `/usr/bin/npx`，不能指向 `/mnt/c/...` 下的 Windows `npx`。

### 视频转写很慢

先运行 `/bilibili-status` 检查登录字幕。登录态有效但视频确实无字幕时，用户确认后才执行本地 CPU ASR；后续默认复用 `knowledge_base/<BV>/transcript.txt`。

### 无法转写或 OCR

依次检查：

```bash
yt-dlp --version
ffmpeg -version
python -c 'import faster_whisper, easyocr, opencc; print("ok")'
```

### TUI 看似停止

查看活动行中的当前阶段和耗时；视频下载、ASR 与 OCR 可能持续较久。需要停止时按一次 `Ctrl+C`，等待界面确认 worker 已结束后再提交新任务。

### API 返回 401、402 或超时

- `401`：检查密钥、endpoint 和模型名称。
- `402`：检查服务商余额。
- 超时：检查网络并适当增大 `DEEPSEEK_TIMEOUT`，默认值为 180 秒。

## 10. 验收命令

```bash
python -m unittest discover -s tests -v
python -m compileall agent backend tools tui security eval
python -m agent.cli --selfcheck
python -m eval.demo_check
python -m eval.demo_check --live
python -m security.redteam
pip check
```
