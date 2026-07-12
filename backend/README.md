# Backend 模块

`DeepSeekBackend` 负责 OpenAI-compatible 请求、流式 SSE 拼接、工具调用归一化和 `usage` 保留。`FakeBackend` 只用于离线自检和确定性测试，不应作为 Demo 任务结果。

文本模型读取 `DEEPSEEK_*`，视觉模型读取 `VISION_*`。Endpoint 和密钥只来自环境变量；日志、trace 与会话文件均不得保存密钥。流式响应必须同时累计文本、碎片化 tool call 参数和最终 token usage。
