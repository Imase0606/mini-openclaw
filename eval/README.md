# Eval 模块

本目录包含工具调用指标、Skill 消融、规划消融、LLM-as-judge 和 Demo Day 一键检查。

```bash
python -m eval.demo_check
python -m eval.demo_check --live
python -m eval.planning_ablation --runs 3
```

`demo_check` 默认离线；`--live` 执行最小 DeepSeek/MiMo 请求并连接官方 filesystem MCP。真实消融复用缓存 transcript，不重新执行 ASR；原始 trace 位于被忽略的 `.mini-openclaw/`，汇总 JSON 跟踪在 `eval/planning_ablation_results.json`。
