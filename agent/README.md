# Agent 模块

`AgentLoop` 是 ReAct 执行引擎，负责 LLM 决策、权限判定、工具回填、compaction 和停止。`memory.py` 提供跨会话状态，`planning.py` 提供单次运行 Todo，`tracer.py` 提供脱敏回放。

关键约束：Todo 不使用全局变量；记忆低于当前用户指令；重复工具调用和无进展都有硬上限。
