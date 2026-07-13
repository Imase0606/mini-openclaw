# Eval 模块

本目录包含工具调用指标、Skill 消融、规划消融、LLM-as-judge 和 Demo Day 一键检查。

```bash
python -m eval.demo_check
python -m eval.demo_check --live
python -m eval.planning_ablation --runs 3
python -m eval.rag_evaluation
python -m eval.rag_evaluation --workspace --reindex
python -m eval.teacher_acceptance
# 本地 persistent 环境可先独立登录；课程 ephemeral 环境由下一条命令进程内扫码
python -m tools.bilibili_auth login
python -m eval.teacher_acceptance --fresh-live
python -m eval.teacher_acceptance --case b3 --artifacts-dir .mini-openclaw/teacher-b3
python -m eval.teacher_acceptance --live --bvid <现场确认的无字幕BV>
python -m eval.teacher_acceptance --subtitle-auth-live --bvid <有内置字幕的公开BV>
python -m eval.skill_ablation
```

`demo_check` 默认离线；`--live` 执行最小 DeepSeek/MiMo 请求并连接官方 filesystem MCP。真实消融复用缓存 transcript，不重新执行 ASR；原始 trace 位于被忽略的 `.mini-openclaw/`，汇总 JSON 跟踪在 `eval/planning_ablation_results.json`。

`rag_evaluation` 默认使用仓库内 6 个小型视频 fixture 和 30 条问题，报告 Recall@K、MRR、nDCG、无答案识别、来源多样性、引用有效率、延迟和上下文缩减，并测量 10k chunk 的 p50/p95。`--workspace` 可评估本地真实缓存；整个过程不调用模型或外部网络。

`teacher_acceptance` 默认离线验证字幕解析、确认式 ASR、三类空内容、提示注入和 OCR 后备。`--fresh-live` 从知识区和生活区/热门流现场抓取候选，B1 要求登录字幕完整且 ASR 调用为 0；B2 连续审计两次并完整预检，只有用户确认后才运行 Whisper，最终来源必须为 ASR。`--live` 和 `--subtitle-auth-live` 保留给人工指定的现场 BV，证据默认写入 `.mini-openclaw/teacher_acceptance/`。`skill_ablation` 每组运行三个固定场景并将原始输出保存到 `.mini-openclaw/eval/`。
