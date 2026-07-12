# mini-OpenClaw（学生 starter 仓库）

<img src="tui/assets/knowledge-terminal-128.png" alt="mini-openclaw 像素知识终端" width="96">

完整的安装、模型配置、TUI、视频提炼与图片分析说明见 [使用指南](docs/user_guide.md)。

> 你将在这 10 天里，把这个骨架填成一个能在命令行里干活的通用智能体。
> 每个模块里都有 `# TODO[DayN]` 标记，告诉你哪天该填哪里。

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

## 目录结构与建设节奏

| 模块 | 你要做什么 | 哪天 |
|------|-----------|------|
| `backend/` | DeepSeek API 客户端（已给 `client.py`，配 key 即用）；Day2 连通后端 + 首个工具 schema | Day1–2 |
| `prompt/` | render_prompt(messages, tools) 对话模板渲染 + parse_tool_calls | Day3 |
| `agent/` | 系统提示词（Day2 起草，Day5 完善）、ReAct 主循环、上下文管理 | Day2, Day5, Day7 |
| `tools/` | read/write/bash → edit/grep/glob → web_fetch/task_list | Day5, Day6, Day7 |
| `mcp/` | 最小 MCP 客户端（stdio + JSON-RPC）| Day8 |
| `skills/` | Skills 加载器 + 你领域的 Skill | Day9 |
| `eval/` | 任务集 + 指标评测 + 消融 | Day7, Day10 |

> 逐日构建目标详见各 `course/dayNN/lab-guide.md`；`grep -rn "TODO\[Day" .` 可看全部施工点。
> 里程碑：**v1（Day6）** 端到端可用 · **v3（Day9）** 可扩展 · **终版（Day10）** 含安全层，Demo Day 展示（占总评 95%）。

## 快速开始

```bash
# 1. Python 环境（agent 侧不吃显存）
conda create -n openclaw python=3.11 && conda activate openclaw
pip install -r requirements.txt

# 2. 先跑通骨架的"假后端"自检（Day1 就能跑）
python -m agent.cli --selfcheck

# 3. 之后每天填对应模块，重跑相关入口
```

### 视频知识库

```bash
# 默认读取转写后自动选择教程、知识、叙事、评论或通用模板
python -m agent.cli "提炼这个B站视频：https://www.bilibili.com/video/BV.../"

# 需要固定报告结构时可手动覆盖类型
python -m agent.cli --video-type tutorial "提炼这个B站视频：https://www.bilibili.com/video/BV.../"
```

视频任务启用最小权限工具集，不会调用通用 `write`、`edit`、`bash` 或 MCP 写工具。产物统一写入 `knowledge_base/<BV>/`。

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

TUI 与 CLI 共用 `AgentRuntime`、权限策略、Memory、Todo、Skill、MCP 和 trace。会话自动脱敏保存，可用 `/sessions` 和 `/resume` 恢复；`/compact` 压缩上下文，`/model` 切换已配置模型。输入 `/help` 查看全部命令。常用快捷键为 `Ctrl+C` 中断、空闲时 `Ctrl+D` 退出、`Shift+Tab` 切换权限模式、`Ctrl+B` 展开 Todo/产物/trace 抽屉。忙碌时输入会进入最多 10 条的 FIFO 队列，`!command` 通过权限层和 Shell 沙箱直接执行。

模型密钥和 endpoint 只从环境变量读取。内置别名为 `deepseek` 和 `mimo`；可用不含密钥的 JSON 扩展 OpenAI-compatible 模型：

```bash
export MINI_OPENCLAW_MODEL_ALIASES='{"local":{"api_key_env":"LOCAL_API_KEY","base_url_env":"LOCAL_BASE_URL","model_env":"LOCAL_MODEL","default_base_url":"http://127.0.0.1:8000/v1","default_model":"local-chat","context_window":32768,"supports_images":false}}'
```

## 里程碑

- **v1（Day6）**：`python -m agent.cli "创建 hello.py 并运行输出当前时间"` 能完成。
- **v3（Day9）**：能加载 MCP server 工具 + 自定义 Skill。
- **终版（Day10）**：含安全层，Demo Day 现场任务。

## 约定

- 全程一个 git 仓库，**按 day 打 tag**（`v1`, `v3`, `final`）。
- 每个模块自带一个 `README.md`，记录你的设计决策（技术文档分数来源）。
