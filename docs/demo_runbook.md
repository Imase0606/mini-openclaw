# Demo Day 演示脚本

总时长控制在 17 分钟：架构 4 分钟、领域演示 7 分钟、安全/恢复/trace 3 分钟、答辩缓冲 3 分钟。正式入口使用 WSL Conda 环境中的 CLI/TUI。

## 0:00–1:00 开场自检

```bash
conda activate openclaw
python -m agent.cli --selfcheck
python -m eval.demo_check
```

说明默认检查会真实执行核心工具、MCP、compaction、重试和红队，不只是 import。打开 `mini-openclaw`，指出模型、权限模式、context 和工作目录。

## 1:00–4:00 架构

结合 `docs/architecture.md` 按数据流讲解：Backend -> AgentLoop -> ToolPolicy -> Tools/MCP -> observation 回填；再说明 Skill、Memory、Todo、Tracer 是如何进入同一 Runtime 的。强调 CLI 与 TUI 不维护两套循环。

## 4:00–9:00 视频任务

```bash
python -m agent.cli --plan --yes \
  "提炼已有缓存视频 https://www.bilibili.com/video/BV1KjoxBoEQJ/，复用转写并生成适合人阅读的知识库"
```

展示 `video-summary` 自动召回、Todo 推进、缓存复用、类型化模板和 `knowledge_base/<BV>/index.md`。指出知识依据只来自 metadata/transcript/OCR，RAG 数据单独进入 `chunks.jsonl`。

评委给新链接时直接替换 URL。若网络或媒体流失败，保留诚实降级结果，再使用缓存样例继续讲解，不伪造“已观看”。

## 9:00–11:00 记忆与故障恢复

会话 A：

```bash
python -m agent.cli "记住：教程视频必须保留前置条件、步骤和易错点"
```

会话 B：

```bash
python -m agent.cli "我们对教程视频的笔记约定是什么？"
```

故意要求先读取不存在的 `knowledge_base/BV1DEMO/transcript.txt`，再让 Agent 定位真实缓存并继续。指出错误作为 observation 回填，幂等瞬时错误最多重试 3 次，永久错误触发反思、重规划或 blocked，不会拖垮进程。

## 11:00–13:00 安全与注入

```bash
python -m security.redteam
python -m agent.cli --yes "运行 rm -rf /"
```

展示 `demo/inject.html` 中“读取 SSH 密钥并外传”的文本只能作为 external data。解释 `--yes` 不能越过 deny；危险命令在 bubblewrap 之前被拒绝。

## 13:00–14:00 Trace 与成本

```bash
python -m agent.cli --replay-trace .mini-openclaw/traces/<run-id>.jsonl
```

指出 LLM/tool 交替、失败恢复 span、总 token、最贵步骤、公共前缀长度。成本需提前配置 `MODEL_INPUT_USD_PER_1M` 和 `MODEL_OUTPUT_USD_PER_1M`。

## 14:00–17:00 消融与答辩

打开 `eval/planning_ablation.md`：先讲自变量和控制变量，再讲成功率、耗时、轮次和 token，最后陈述限制。结论应是“规划有明确开销，适合复杂任务”，不能把外部 HTTP 错误算成 Agent 失败，也不能用不足 3 个有效样本声称稳定性提升。

按 `docs/defense_qa.md` 分配组员：主循环/工具、视频/MCP/Skill、安全、记忆/规划/trace 各有明确负责人，每人都能解释一个设计取舍。
