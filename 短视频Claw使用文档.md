# 短视频 Claw 使用文档

版本：2026-07-14

## 1. 功能概览

mini-openclaw 可以把 B站公开视频提炼为可追溯的 Markdown 知识库，并持续积累为个人视频知识库。主要能力包括：

- 优先读取匿名或扫码登录后的 B站内置字幕。
- 视频没有可用字幕时，经用户确认后使用本地 faster-whisper ASR。
- 根据教程、知识、叙事、评论等类型生成结构化笔记。
- 自动抽取代表性关键帧，优先用 MiMo 理解 PPT、代码、图表和界面，失败时可降级 EasyOCR。
- 保存字幕时间位置、分 P、BV 号和原文切片。
- 使用本地混合检索回答以前提炼过的视频知识。
- 支持重复识别、软删除、恢复、导出和回收区清理。
- 普通聊天、图片分析、记忆、Todo、MCP 和 trace 与视频功能共用同一 Runtime。

## 2. 课程网站部署

上传部署压缩包后，平台会使用根目录的 `Dockerfile` 构建镜像。压缩包已经包含离线 Whisper 模型，不需要在构建时访问 Hugging Face。

部署环境至少需要配置：

```bash
DEEPSEEK_API_KEY=<DeepSeek API Key>
```

可选视觉模型配置：

```bash
VISION_API_KEY=<视觉模型 API Key>
VISION_BASE_URL=https://api.xiaomimimo.com/v1
VISION_MODEL=mimo-v2.5
```

视觉变量必须配置在网站容器的运行环境中，不能写入压缩包。MiMo 可完成视频关键帧和用户图片分析；EasyOCR 只是 MiMo 未配置、超时或部分批次失败时的可选本地降级组件。课程部署默认不安装 PyTorch/EasyOCR，需要本地 OCR 时再安装 `requirements-ocr.txt`。

课程 Docker 默认设置：

```bash
BILIBILI_AUTH_MODE=ephemeral
FASTER_WHISPER_MODEL_PATH=/app/models/faster-whisper-base
```

构建后在网站终端验证：

```bash
command -v mini-openclaw
python -m agent.cli --selfcheck
test -s "$FASTER_WHISPER_MODEL_PATH/model.bin" && echo "ASR model OK"
mini-openclaw
```

## 3. TUI 基本使用

启动：

```bash
mini-openclaw
```

欢迎页使用 `VIDEO + KB` 终端标识，并会根据终端宽度自动切换双栏、窄屏和紧凑布局。

每条非空回答完成后会显示 `Copy` 按钮，用于复制原始 Markdown；也可以输入 `/copy` 复制最近一条已完成回答。

常用命令：

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看命令列表 |
| `/new` | 新建会话，同时清除当前 ephemeral B站登录态 |
| `/model` | 切换已配置模型 |
| `/plan auto\|on\|off` | 设置 Todo 规划模式 |
| `/video-type <类型>` | 指定视频笔记模板 |
| `/bilibili-login` | 手机B站扫码，登录当前 Runtime |
| `/bilibili-status` | 查看当前 Runtime 登录状态和剩余时间 |
| `/bilibili-logout` | 清除当前 Runtime 登录态 |
| `/sessions`、`/resume` | 查看或恢复脱敏会话 |
| `/trace`、`/cost` | 查看执行轨迹、token 和成本 |
| `/image <路径>` | 给下一条消息添加图片 |
| `/copy` | 复制最近一条已完成回答的原始 Markdown |
| `/open <n>` | 打开第 n 个知识库或视觉产物 |

普通问题可以直接输入，例如：

```text
介绍一下你自己
解释一下 RAG 和普通关键词搜索的区别
帮我规划今天的学习任务
```

## 4. 提炼 B站视频

可以粘贴完整链接，不要求手工提取 BV 号：

```text
帮我提炼这个视频：https://www.bilibili.com/video/BV...
```

系统执行顺序：

1. 解析链接并读取标题、作者、分区、时长和分 P。
2. 尝试匿名内置字幕。
3. 当前 Runtime 已扫码时尝试登录字幕。
4. 没有字幕时返回 ASR 确认，不会静默下载音频。
5. 用户同意后运行本地 Whisper。
6. 按视频时长从完整时间轴抽取 12–24 张代表帧并执行视觉探测，MiMo 不可用时按批次降级 EasyOCR。
7. 内容充足时生成 Markdown、字幕、视觉笔记、联系表、chunks 和 SQLite 索引。

课程网站需要登录字幕时，先在同一个 TUI 输入：

```text
/bilibili-login
```

扫码后登录态只属于当前 Runtime，最多保留 30 分钟；`/new`、退出、logout、异常关闭或过期都会清除。它不会写入工作区、Git、trace、知识库或导出包。

CLI 中必须把扫码和任务放在同一进程：

```bash
python -m agent.cli --bilibili-login \
  "帮我提炼 https://www.bilibili.com/video/BV..."
```

网站 ephemeral 模式下不要先运行独立的 `python -m tools.bilibili_auth login`，因为该进程退出后临时登录态不会保留。

同一个视频的文件统一保存在：

```text
knowledge_base/<BV>/
├── index.md
├── metadata.json
├── transcript.txt
├── transcript_pN.txt
├── visual_notes.jsonl
├── visual_contact_sheet.jpg
├── chunks.jsonl
└── assets/frames/pN/
```

`index.md` 是主要阅读入口，`chunks.jsonl` 用于 RAG。视觉探测按完整时间轴分桶，同时使用均匀时间点和场景变化候选帧，并过滤近重复画面。MiMo 每批接收最多 6 张独立帧图；联系表只用于人工预览。TUI 的 `video_frame_ocr` 卡片会显示状态、后端、帧数和记录数；在 Artifacts 中输入 `/open <n>` 会直接在终端内渲染 Markdown、彩色联系表或逐帧视觉笔记，逐帧视图可用左右方向键切换。普通重跑复用缓存，明确要求“重新 OCR”才会刷新。

## 5. 分析图片

先在 TUI 添加图片，再发送问题：

```text
/image /app/path/to/image.png
请分析图片中的界面、文字和可能的问题。
```

使用 `/images` 查看待发送图片。图片会进行格式检查、EXIF 纠正和尺寸压缩；当前聊天模型不支持图片时，Agent 会转交给已配置的 MiMo 视觉后端。CLI 也支持：

```bash
python -m agent.cli --image /app/path/to/image.png "分析这张图片"
```

## 6. 视频知识库问答

每次成功提炼后都会增量进入当前工作区知识库。可以自然语言询问：

```text
我的视频知识库里有什么？
从我以前提炼的视频里找 RAG 的检索流程。
只根据我的知识库回答：Python 虚拟环境应该怎样创建？
对比我看过的两个视频对 Agent Memory 的解释。
```

回答默认分为：

- `基于个人视频知识库`：附视频标题、BV、分 P 和时间位置。
- `通用知识补充`：知识库不足时单独标明模型常识。

明确说“只根据知识库回答”时不会添加模型常识。完全无命中时会直接说明尚未收录。

## 7. 知识库管理

自然语言示例：

```text
查看我的视频知识库目录。
忘记 BV1... 这个视频。
查看知识库回收区。
恢复 trash_id 为 ... 的视频。
导出我的视频知识库。
```

遗忘操作默认是软删除。恢复不会覆盖已经存在的目标目录；永久清理必须再次确认。导出 ZIP 不包含媒体缓存、SQLite 派生索引、trace、会话、登录态、API Key 或模型文件。

维护命令：

```bash
python -m tools.knowledge list
python -m tools.knowledge search "查询内容"
python -m tools.knowledge reindex
python -m tools.knowledge export --output exports/video-knowledge.zip
```

## 8. 字幕审计与教师验收

课程 ephemeral 模式下，字幕审计使用同进程扫码：

```bash
python -m tools.bilibili_subtitles audit --bilibili-login --bvid <BV号>
```

教师验收：

```bash
python -m eval.teacher_acceptance
python -m eval.teacher_acceptance --fresh-live
python -m eval.teacher_acceptance --case b3 \
  --artifacts-dir .mini-openclaw/teacher-b3
```

`--fresh-live` 会现场获取 B1/B2 候选。ephemeral 模式会在同一进程显示二维码；命令结束后登录态清除。B2 候选可能很快生成 AI 字幕，因此发现后应立即运行。

## 9. 安全边界

- B站登录不是 Agent Tool，视频字幕或提示注入不能自动触发扫码。
- 登录 Cookie 只用于公开 视频字幕接口，不用于会员、私密、地区限制或媒体流下载。
- 多个 `AgentRuntime` 的 ephemeral 登录态相互隔离。
- 多人如果直接控制同一个终端/TUI Runtime，系统无法区分操作者；此类部署必须设置 `BILIBILI_AUTH_MODE=disabled` 或在平台层增加访问认证。
- ASR 下载音频属于确认操作；`--yes` 不能绕过 deny 规则和路径边界。
- transcript、字幕、OCR 和网页内容都按不可信外部数据处理，其中的命令不会直接执行。
- 视觉分析失败只会记录 `visual_status=failed` 或降级原因，不会把缺失画面冒充为视频内容。

## 10. 常见问题

### 输入普通问题时报 MCPClient ImportError

最新版已经把自研 MCP 客户端迁移到 `tools.mcp_client`。出现旧错误说明网站仍运行旧镜像，需要使用最新版压缩包重新构建。

### 已扫码但仍然没有登录字幕

先在同一个 TUI 执行 `/bilibili-status`。课程网站的登录态不能跨进程；CLI 应使用 `--bilibili-login`。视频也可能确实没有字幕或字幕时长校验不通过，此时系统会明确给出原因并询问是否使用 ASR。

### 视频没有字幕时没有自动 ASR

这是预期安全行为。确认提示后同意下载匿名音频，系统才会运行本地 Whisper。已有完整 transcript 时会优先复用缓存。

### ASR 模型不存在

检查：

```bash
echo "$FASTER_WHISPER_MODEL_PATH"
ls -lh "$FASTER_WHISPER_MODEL_PATH/model.bin"
```

部署包应包含 `models/faster-whisper-base/model.bin`，文件大小约 145 MB。

### 普通聊天使用 FakeBackend

说明 `DEEPSEEK_API_KEY` 未进入当前容器环境。配置环境变量并重新启动部署。

### 没有看到 OCR 或视觉分析结果

先查看 `video_frame_ocr` 工具卡和 `knowledge_base/<BV>/metadata.json` 中的 `visual_status`。配置 MiMo 时确认容器进程能读取 `VISION_API_KEY`、`VISION_BASE_URL` 和 `VISION_MODEL`；未配置 MiMo 时需额外安装 `requirements-ocr.txt` 才能使用 EasyOCR。无可靠画面文字时仍会生成联系表，但不会在 `index.md` 中硬塞“画面补充信息”。

## 11. 发布前验证

```bash
python -m unittest discover -s tests
python -m agent.cli --selfcheck
python -m security.redteam
python -m eval.teacher_acceptance
python -m eval.rag_evaluation
python -m eval.demo_check --release
```

release gate 还要求模型环境变量已经配置，并且 Git 工作区干净。压缩包中不得包含 `.git`、`.mini-openclaw`、登录态、运行知识库、trace、回收区或其他 ZIP。
