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

首次使用 EasyOCR 时会下载 OCR 模型；无字幕视频首次转写时会下载 faster-whisper 模型。CPU 环境可按需安装 CPU 版 PyTorch：

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

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
2. 优先读取公开字幕；无字幕时用 faster-whisper 转写音频。
3. 必要时抽取关键帧并用 EasyOCR 识别 PPT、代码或图表文字。
4. 根据视频类型生成学习笔记和 RAG 切块。
5. 已存在完整知识库时直接复用，除非明确要求重新生成。

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

其中 `index.md` 是主要阅读入口，`chunks.jsonl` 用于后续 RAG。程序不会长期保存完整音视频。仅支持无需登录的公开内容，不绕过会员、私密、地区或平台访问限制。

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

无字幕视频需要本地 CPU ASR，首次还会下载模型。后续默认复用 `knowledge_base/<BV>/transcript.txt`，不要删除缓存，也不要要求强制刷新。

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
