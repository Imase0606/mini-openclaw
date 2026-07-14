# Demo Day 验收清单

本清单对应课程 95 分评分表。演示前在 WSL `openclaw` 环境运行 `python -m eval.demo_check --release`，所有项必须为 `ok`。

| 评分项 | 现场证据 | 仓库证据 |
| --- | --- | --- |
| A 系统完整性 | `--selfcheck`、启动 TUI | `docs/architecture.md`、各模块 README |
| B 任务完成 | 现场发现 B1/B2 链接并生成知识库 | `eval.teacher_acceptance --fresh-live` |
| C 主循环/规划 | `--plan` 展示 Todo、正确终止 | `agent/loop.py`、规划测试 |
| D MCP/Skills | echo/calc MCP 与 `video-summary` 召回 | `mcp/`、`skills/video-summary/` |
| D 登录字幕 | 用户扫码后真实内置字幕命中，ASR 调用为 0 | `bilibili_auth`、`bilibili_subtitles` |
| D 个人知识扩展 | 自然语言检索历次视频并附回看位置 | `kb_search`、`personal-video-knowledge` |
| E 知识治理 | 展示重复状态、软删除与恢复，确认永久清理受保护 | `kb_catalog`、管理 Skill、治理测试 |
| E 记忆/鲁棒性 | 两个进程验证记忆；故意错误路径后恢复 | Memory、compaction、重试测试 |
| F 安全 | 注入页面和危险命令均被拦截 | `security/redteam_report.md` |
| G 理解/答辩 | 回放 trace，指出最贵步骤和设计取舍 | `docs/defense_qa.md` |
| H 消融/文档 | 展示每组至少 3 次的对比表 | `eval/planning_ablation.md` |

## 上场前 30 分钟

- `which python` 必须指向 `/home/imase/miniconda3/envs/openclaw/bin/python`。
- 确认 `bwrap --version` 和 `ffmpeg -version` 正常。
- 确认 DeepSeek/MiMo Key 仅存在于环境变量，并有足够余额。
- 运行完整 unittest、redteam、默认 demo check 和 `--release` 检查。
- 运行 `python -m eval.teacher_acceptance --fresh-live`，现场确认知识区 B1 和无可用字幕 B2；缓存样例只作为网络故障后的讲解后备。
- 清空无关终端，预先记下最后一个成功 trace 路径。
- 确认 `v1`、`v3`、`final` tags 已推送，工作树干净。
- 运行 `python -m eval.rag_evaluation`，确认 Recall@K、MRR、nDCG、无答案识别和 10k chunk 延迟达标。
- 运行 `python -m eval.teacher_acceptance`，确认字幕、ASR、三类空内容、注入和 OCR 后备 5 项全部通过。
- 课程镜像确认 `BILIBILI_AUTH_MODE=ephemeral`；由 `--fresh-live` 或 TUI 在当前进程扫码，不能用独立 status 判断另一个进程的登录态。
- 重新部署前删除旧服务器登录态，并在B站登录设备管理中撤销此前共享容器创建的网页会话。
- 若多人会附着到同一个终端/TUI Runtime，改用 `BILIBILI_AUTH_MODE=disabled`；ephemeral 只能隔离不同 Runtime。
- 运行 B3 持久证据命令，现场打开诊断 `index.md` 和空 `chunks.jsonl`。
- 确认 `.dockerignore` 排除 trace、运行知识库、ZIP 和缓存，但没有排除离线 Whisper 模型。

## 验收命令

```bash
python -m agent.cli --selfcheck
python -m unittest discover -s tests -v
python -m security.redteam
python -m eval.teacher_acceptance
python -m eval.teacher_acceptance --fresh-live
python -m eval.teacher_acceptance --case b3 --artifacts-dir .mini-openclaw/teacher-b3
python -m eval.teacher_acceptance --subtitle-auth-live --bvid <BV>
python -m eval.demo_check
python -m eval.demo_check --live
python -m eval.demo_check --release
git status --short && git tag --list
```
