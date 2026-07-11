# 可观测性调试记录

## 问题

Day9 接入 Tracer 后，LLM span 的 token 始终为 0。API 原始响应包含 `usage`，但 `DeepSeekBackend.chat()` 只把 `choices[0].message` 传给 `_normalize()`，顶层 `usage` 被丢弃。

## 定位

工具 span 正常出现，LLM span 也有耗时和输出，只有 `usage.total_tokens` 为 0。这排除了 Tracer 未接线，问题位于后端响应归一化边界。

## 修复

后端先保存完整 JSON，再分别归一化 message、usage 和 model。新增回归测试使用假响应确认 `prompt_tokens=11`、`completion_tokens=3`、`total_tokens=14` 能进入内部消息。

## 优化

AgentLoop 缓存排序后的工具 schema；稳定 system prompt、项目记忆和 Skill 位于请求前部，动态 Todo 只临时追加在尾部。Tracer 为每个 LLM span 记录与上一轮请求的 `prefix_chars`，用于验证公共前缀增长。
