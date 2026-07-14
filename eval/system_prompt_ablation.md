# System Prompt 消融实验

- 视频：[BV1YiNj6nE7n](https://www.bilibili.com/video/BV1YiNj6nE7n/)
- 日期：2026-07-14
- 模型：DeepSeek Chat，temperature=0
- 自变量：system-prompt 文本 + API tools 参数（同时有 / 同时无）
- 每组运行次数：3

## 原始结果

| 模式 | 运行 | 结果 | 耗时(s) | LLM步 | 工具步 | Token |
|---|---:|---|---:|---:|---:|---:|
| system | 1 | success | 6.384 | 3 | 2 | 16071 |
| system | 2 | success | 6.839 | 3 | 2 | 16074 |
| system | 3 | success | 6.738 | 3 | 2 | 16094 |
| no-system | 1 | agent_failure | 1.748 | 1 | 0 | 160 |
| no-system | 2 | agent_failure | 2.155 | 1 | 0 | 184 |
| no-system | 3 | agent_failure | 2.245 | 1 | 0 | 185 |

## 汇总

- **有 system-prompt + 有 tools**：有效样本 3/3，外部错误 0，成功率 100%，平均耗时 6.654s，平均 LLM 步 3.0，平均 Token 16079.667。
- **无 system-prompt + 无 tools**：有效样本 3/3，外部错误 0，成功率 0%，平均耗时 2.049s，平均 LLM 步 1.0，平均 Token 176.333。

## 消融总结

- **变量**：system-prompt 文本 + API tools 参数（同时有 / 同时无），其余（模型 deepseek-v4-flash、温度 0、任务文本、视频 BV1YiNj6nE7n）固定
- **结果**：有 system-prompt + 有 tools = 3/3，无 system-prompt + 无 tools = 0/3
- **归因**：无系统提示词且无工具时，模型无法获取视频真实内容，只能根据 BV 号自行编造一个结构完整但内容完全虚构的回答。模型在纯文本输出中扮演工具调用流程，但无任何真实 function call 发生。
