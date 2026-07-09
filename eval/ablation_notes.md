# 消融草稿（Day3 · 样本轨迹）
- 变量：system-prompt（有 / 无），其余（任务集、模型 deepseek-v4-flash）固定
- 结果：有=1.00 / 无=0.00；token：有更高（多了工具说明）
- 归因：无 system-prompt 时 agent 不知道 <tool_call> 约定 → 从不调工具 → 全失败
- 局限：样本量太小（各 2 条）、样本是构造的；D4 起用真轨迹、每组多次运行取均值再下结论