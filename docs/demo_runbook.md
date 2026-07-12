# Demo Day 演示脚本

## 0. 开场自检

```bash
conda activate openclaw
python -m agent.cli --selfcheck
python -m eval.demo_check
```

随后运行 `mini-openclaw`，演示 `/resume`、`/compact`、`/model`、`Shift+Tab` 权限切换、忙碌任务排队和 `Ctrl+B` 详情抽屉；CLI 继续用于自动化验收。输入 `!echo hello` 展示直接 Shell 仍需确认，再切换 plan 模式证明该命令会被执行层拒绝。

用 3-5 分钟结合 `docs/architecture.md` 说明 backend、主循环、工具、MCP、Skill、安全、记忆、规划和 trace。

## 1. 跨会话记忆

```bash
python -m agent.cli "记住：教程类视频必须保留前置条件、操作步骤和易错点"
python -m agent.cli "我们对教程视频的笔记约定是什么？"
```

第一条确认 `remember`，第二条使用新进程验证召回。展示 `.mini-openclaw/memory.json` 只保存脱敏短记忆且已被 Git 忽略。

## 2. 视频 Skill 与规划

```bash
python -m agent.cli --plan --yes \
  "提炼已有缓存视频 https://www.bilibili.com/video/BV1KjoxBoEQJ/，不要重新 ASR"
```

观察 `video-summary` 召回、Todo 更新、缓存复用和教程模板。现场可故意给一次错误路径，展示反思、重规划或 blocked，而不是进程崩溃。

## 3. Trace 与成本

复制任务结束时打印的 trace 路径：

```bash
python -m agent.cli --replay-trace .mini-openclaw/traces/<run-id>.jsonl
```

指出 LLM/tool 交替、最贵步骤、总 token、公共前缀长度和失败恢复 span。供应商价格需提前配置两个 `MODEL_*_USD_PER_1M` 环境变量。

## 4. 安全演示

```bash
python -m security.redteam
python -m agent.cli --yes "运行 rm -rf /"
```

危险命令在进入 bubblewrap 前即被拒绝。不要在演示中使用真实密钥或隐私数据。

## 5. 消融与答辩

打开 `eval/planning_ablation.md`，解释已测得的规划轮次/token 开销，以及 HTTP 402 导致稳定性样本不足的实验限制；不要把外部错误算作 Agent 失败。答辩重点：Tool 与 Skill 区别、外部数据为何不能只靠 prompt、防死循环、记忆与 RAG 的边界、trace 如何定位 usage 丢失。
