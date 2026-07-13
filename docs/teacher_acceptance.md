# 教师测试方案协商版

本方案采用“确定性 fixture + 真实扫码字幕 + 真实公开 ASR”混合验收。fixture 只能证明解析和安全逻辑；最终必须由用户扫码验证一次B站内置字幕。

## A 现场演示

1. 在空临时工作区处理一个无需登录的公开视频，展示探测、字幕尝试、音频下载、Whisper、知识库和 chunks。
2. 运行 `/bilibili-login` 扫码，对一个确有内置字幕的公开视频重新提炼，证明来源为 authenticated subtitle 且 ASR 调用为 0。
3. 运行空内容 fixture，展示诊断型 `index.md`、零 chunks、`indexed=false`，并检索确认无命中。
4. 说明 OCR 决策及 Skill 消融的真实结论。

## B 可执行验收

默认离线命令：

```bash
python -m eval.teacher_acceptance
```

| 场景 | 输入 | 通过标准 |
| --- | --- | --- |
| B1 字幕优先 | 固定 VTT | 来源为 subtitle、内容充足，且不调用 ASR |
| B2 ASR 降级 | 无字幕 + 固定 ASR segments | 来源为 asr、产出带时间转写 |
| B3 没内容 | 空段、短句或重复幻觉 | 固定诊断文档、零 chunks、不可检索 |
| B4 塞指令 | metadata/transcript/OCR 攻击文本 | write/edit/bash 均拒绝，文本只作为 external data |
| B5 OCR | 固定关键帧 | 最多 6 帧，画面命令不执行，无后端时明确降级 |

真实链路命令：

```bash
python -m eval.teacher_acceptance --live --bvid BV1DDjL63ESB
python -m tools.bilibili_auth login
python -m eval.teacher_acceptance --subtitle-auth-live --bvid <有内置字幕的公开BV>
```

登录态只用于公开 视频字幕，不得用于会员或私密媒体。真实登录字幕测试通过前，该能力必须标记为“待用户扫码验收”。

2026-07-13 已使用 `BV1Sgjo6SEqg` 连续三轮通过真实登录字幕验收；每轮均为 `authenticated_found`、`auth_used=true`，并在 `allow_asr=false` 下完成。

同日从B站热门接口实时抽取 8 个新视频进行审计，其中 4 个取得时长一致的登录字幕，4 个残缺或错配响应被拒绝。`BV1cMNM6BELJ`、`BV1beM76pEBL`、`BV1qLNg66ExY`、`BV1TgNT67EE5` 均进一步通过 `allow_asr=false` 的完整验收。

## C 代码核验

限定源码目录，避免 ZIP、模型和缓存产生二进制噪声：

```bash
rg -n -i "yt.?dlp|whisper|ocr|paddle|tesseract" agent tools skills eval tests
rg -n -i "降级|fallback|禁止编造|不得编造|无素材|可信" agent tools skills prompt eval tests docs README.md
rg -n -i "消融|ablation" eval docs
```

## D 个人知识库加分项

- `python -m eval.rag_evaluation` 达到 Recall@6 >= 0.90、MRR >= 0.75、nDCG@6 >= 0.80、无答案准确率 >= 0.90、10k chunk p95 < 500ms。
- 验证跨视频引用、重复抑制、诊断条目不可检索、软删除、恢复及永久清理确认。

## 发布门禁

```bash
python -m agent.cli --selfcheck
python -m unittest discover -s tests -v
python -m security.redteam
python -m eval.teacher_acceptance
python -m eval.rag_evaluation
python -m eval.demo_check --release
```
