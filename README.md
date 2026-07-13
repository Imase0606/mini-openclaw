# mini-OpenClaw

<img src="tui/assets/knowledge-terminal-128.png" alt="mini-openclaw 像素知识终端" width="96">

完整的安装、模型配置、TUI、视频提炼与图片分析说明见 [使用指南](docs/user_guide.md)。

> 面向 B站公开视频的知识提炼 Agent，同时保留通用文件、Shell、MCP、记忆、规划和可观测能力。

## 这是什么

mini-OpenClaw 是一个 Claude Code 式的命令行 Agent：
一个**主循环**反复调用**大模型后端**，模型输出**工具调用**（read/write/bash/…），
主循环执行工具、把结果喂回模型，直到任务完成。再叠加 **MCP**（可插拔外部工具）、
**Skills**（可加载领域能力）和**安全层**（权限/沙箱/注入防护）。

```
你的请求 ──► [主循环 loop.py] ──► [后端 server.py ──► 大模型]
                  ▲   │  模型输出 <tool_call>{...}</tool_call>
                  │   ▼
            tool result ◄── [工具分发：read/write/bash/edit/grep/...]
                              ├── 内置工具 (tools/)
                              ├── MCP 工具 (mcp/)
                              └── Skills (skills/)
```

## 项目结构

| 模块 | 职责 |
|------|------|
| `backend/` | DeepSeek/MiMo 的 OpenAI-compatible 客户端与图像输入 |
| `agent/` | ReAct 主循环、上下文、权限、记忆、规划、会话和 trace |
| `tools/` | 文件、Shell、网页与视频提取工具 |
| `mcp/` | stdio JSON-RPC MCP 客户端 |
| `skills/` | 按任务召回的领域工作流 |
| `tui/` | 与 CLI 共用 Runtime 的 Textual 交互界面 |
| `security/`、`eval/` | 红队测试、自动验收与消融实验 |

## 快速开始

```bash
# 1. Python 环境（agent 侧不吃显存）
conda create -n openclaw python=3.11 && conda activate openclaw
pip install -r requirements.txt

# 可选：需要关键帧 OCR 时安装 CPU-only OCR 依赖
pip install -r requirements-ocr.txt

# 2. 运行离线自检与 Demo Day 运行时验收
python -m agent.cli --selfcheck
python -m eval.demo_check
python -m eval.teacher_acceptance
```

课程部署平台默认只安装 `requirements.txt`，不会下载 PyTorch/CUDA；视频元数据、扫码登录字幕、确认式 ASR、知识库和 MiMo 图像输入仍可用。EasyOCR 仅用于 PPT、代码和图表的关键帧文字补充，未安装时工具会明确降级。

`requirements.txt` 会安装项目本身并注册 `mini-openclaw` 命令，因此课程平台即使使用自动 Dockerfile 也能获得 TUI 入口。部署压缩包内置 `models/faster-whisper-base`，自定义 `Dockerfile` 会校验模型和命令入口，不在构建期或运行期访问 Hugging Face。在平台提供的交互终端中运行 `mini-openclaw`；备用入口为 `python -m tui`。Textual TUI 需要真实 TTY，不能作为普通 HTTP 网页直接打开。

### 视频知识库

```bash
# 首次使用B站内置字幕时扫码登录一次
python -m tools.bilibili_auth login
python -m tools.bilibili_auth status

# 默认读取转写后自动选择教程、知识、叙事、评论或通用模板
python -m agent.cli "提炼这个B站视频：https://www.bilibili.com/video/BV.../"

# 需要固定报告结构时可手动覆盖类型
python -m agent.cli --video-type tutorial "提炼这个B站视频：https://www.bilibili.com/video/BV.../"
```

字幕获取顺序为匿名公开字幕、用户扫码登录后的内置字幕、用户确认后的本地 ASR。扫码登录后再次提炼已有 ASR 视频时，系统会自动检查登录字幕；命中后原子替换旧 ASR，未命中或接口异常则保留旧缓存且不重复运行 Whisper。未登录、登录过期、视频无字幕和接口错误会分别报告。视频任务启用最小权限工具集，不会调用通用 `write`、`edit`、`bash` 或 MCP 写工具。产物统一写入 `knowledge_base/<BV>/`。

每次 `kb_write` 成功后会增量更新本地个人视频知识索引。之后可以直接询问历次提炼内容：

```bash
python -m agent.cli "从我之前提炼的视频里找：Windows 安装 Claude Code 有哪些易错点？"
python -m agent.cli "我的视频知识库里有哪些教程？"

# 升级旧知识库或修复派生索引，不会重新下载和 ASR
python -m tools.knowledge --reindex
python -m tools.knowledge list
python -m tools.knowledge search "Agent 记忆"
```

个人知识问答只开放 `kb_search`、`kb_catalog` 等只读工具。回答优先依据知识库并附视频位置；知识不足时，模型常识会放在独立的“通用知识补充”部分。

知识库支持自然语言软删除、恢复和导出，例如“忘记视频 BV...”“恢复回收区中的 BV...”“导出我的知识库”。治理 Tool 会触发确认；软删除可恢复，只有明确清理回收区才会永久删除。

### 安全确认

`write`、`edit`、`bash`、`web_fetch` 和 MCP 工具默认需要终端确认。自动化场景可显式传入 `--yes`，但工作区边界、敏感路径保护、Shell 沙箱和网络白名单仍然生效：

```bash
python -m agent.cli --yes "运行 echo hello"
WEB_FETCH_ALLOW_HOSTS=docs.example.org python -m agent.cli "总结 https://docs.example.org/guide"
python -m security.redteam
```

WSL/Linux 会优先使用 bubblewrap 提供只读系统、工作区可写和禁网沙箱；缺失时使用保守降级防护：

```bash
sudo apt install bubblewrap
bwrap --version
```

### 记忆、规划与 Trace

```bash
# 明确要求后，remember 会在确认后写入私有运行时记忆
python -m agent.cli "记住：教程视频应保留完整操作步骤"

# 强制规划或关闭规划
python -m agent.cli --plan "完成一个多步骤任务"
python -m agent.cli --no-plan "完成一个简单任务"

# 默认 trace 写到 .mini-openclaw/traces；可回放 token、成本和耗时
python -m agent.cli --replay-trace .mini-openclaw/traces/<trace>.jsonl
python -m eval.demo_check
```

成本估算通过 `MODEL_INPUT_USD_PER_1M` 和 `MODEL_OUTPUT_USD_PER_1M` 配置；未配置时仍统计 token，但不猜测供应商价格。

### 交互式终端界面

```bash
pip install -e .
mini-openclaw
# 兼容入口
python -m tui
```

TUI 与 CLI 共用 `AgentRuntime`、权限策略、Memory、Todo、Skill、MCP 和 trace。会话自动脱敏保存，可用 `/sessions` 和 `/resume` 恢复；`/bilibili-login` 扫码登录内置字幕，`/bilibili-status` 查看状态，`/bilibili-logout` 删除本地登录态。输入 `/help` 查看全部命令。常用快捷键为 `Ctrl+C` 中断、空闲时 `Ctrl+D` 退出、`Shift+Tab` 切换权限模式、`Ctrl+B` 展开 Todo/产物/trace 抽屉。

模型密钥和 endpoint 只从环境变量读取。内置别名为 `deepseek` 和 `mimo`；可用不含密钥的 JSON 扩展 OpenAI-compatible 模型：

```bash
export MINI_OPENCLAW_MODEL_ALIASES='{"local":{"api_key_env":"LOCAL_API_KEY","base_url_env":"LOCAL_BASE_URL","model_env":"LOCAL_MODEL","default_base_url":"http://127.0.0.1:8000/v1","default_model":"local-chat","context_window":32768,"supports_images":false}}'
```

## 里程碑

- **v1（Day6）**：`python -m agent.cli "创建 hello.py 并运行输出当前时间"` 能完成。
- **v3（Day9）**：能加载 MCP server 工具 + 自定义 Skill。
- **终版（Day10）**：含安全层，Demo Day 现场任务。

Demo Day 的评分证据、时间安排和答辩准备见 [教师测试协商版](docs/teacher_acceptance.md)、[验收清单](docs/demo_checklist.md)、[演示脚本](docs/demo_runbook.md) 与 [答辩要点](docs/defense_qa.md)。正式提交前运行：

```bash
python -m unittest discover -s tests -v
python -m eval.teacher_acceptance
python -m eval.rag_evaluation
python -m eval.demo_check --release
```

## 约定

- 全程一个 git 仓库，**按 day 打 tag**（`v1`, `v3`, `final`）。
- 每个模块自带一个 `README.md`，记录你的设计决策（技术文档分数来源）。
