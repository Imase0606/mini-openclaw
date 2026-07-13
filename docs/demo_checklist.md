# Demo Day 验收清单

本清单对应课程 95 分评分表。演示前在 WSL `openclaw` 环境运行 `python -m eval.demo_check --release`，所有项必须为 `ok`。

| 评分项 | 现场证据 | 仓库证据 |
| --- | --- | --- |
| A 系统完整性 | `--selfcheck`、启动 TUI | `docs/architecture.md`、各模块 README |
| B 任务完成 | 随机 B站链接生成知识库 | `knowledge_base/BV1j9MP6wEV9/` |
| C 主循环/规划 | `--plan` 展示 Todo、正确终止 | `agent/loop.py`、规划测试 |
| D MCP/Skills | echo/filesystem MCP 与 `video-summary` 召回 | `mcp/`、`skills/video-summary/` |
| D 登录字幕 | 用户扫码后真实内置字幕命中，ASR 调用为 0 | `bilibili_auth`、`bilibili_subtitles` |
| D 个人知识扩展 | 自然语言检索历次视频并附回看位置 | `kb_search`、`personal-video-knowledge` |
| E 知识治理 | 展示重复状态、软删除与恢复，确认永久清理受保护 | `kb_catalog`、管理 Skill、治理测试 |
| E 记忆/鲁棒性 | 两个进程验证记忆；故意错误路径后恢复 | Memory、compaction、重试测试 |
| F 安全 | 注入页面和危险命令均被拦截 | `security/redteam_report.md` |
| G 理解/答辩 | 回放 trace，指出最贵步骤和设计取舍 | `docs/defense_qa.md` |
| H 消融/文档 | 展示每组至少 3 次的对比表 | `eval/planning_ablation.md` |

## 上场前 30 分钟

- `which python` 必须指向 `/home/imase/miniconda3/envs/openclaw/bin/python`。
- 确认 `which npx` 为 Linux 路径，`bwrap --version` 和 `ffmpeg -version` 正常。
- 确认 DeepSeek/MiMo Key 仅存在于环境变量，并有足够余额。
- 运行完整 unittest、redteam、默认 demo check 和 `--release` 检查。
- 准备一个缓存视频和一个未缓存公开视频；缓存样例避免现场网络波动。
- 清空无关终端，预先记下最后一个成功 trace 路径。
- 确认 `v1`、`v3`、`final` tags 已推送，工作树干净。
- 运行 `python -m eval.rag_evaluation`，确认 Recall@K、MRR、nDCG、无答案识别和 10k chunk 延迟达标。
- 运行 `python -m eval.teacher_acceptance`，确认字幕、ASR、空内容、注入和 OCR 后备 5 项全部通过。
- 运行 `python -m tools.bilibili_auth status`；若演示内置字幕，必须为 `valid`，并提前确认测试 BV 确有字幕。

## 验收命令

```bash
python -m agent.cli --selfcheck
python -m unittest discover -s tests -v
python -m security.redteam
python -m eval.teacher_acceptance
python -m eval.teacher_acceptance --subtitle-auth-live --bvid <BV>
python -m eval.demo_check
python -m eval.demo_check --live
python -m eval.demo_check --release
git status --short && git tag --list
```
