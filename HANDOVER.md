# Project Handover

## Current State

mini-openclaw is a Python 3.11 CLI/TUI agent for extracting public Bilibili videos into traceable Markdown knowledge bases and querying them through local RAG. The repository is functionally complete for the current course milestone.

Canonical workspace:

```text
Windows: D:\develop\aiFrontierPractice\mini-openclaw
WSL: /mnt/d/develop/aiFrontierPractice/mini-openclaw
Conda environment: openclaw
Python: /home/imase/miniconda3/envs/openclaw/bin/python
Branch: master
Local HEAD: 5a34edec4c626ce48bd7a8e264641513cc6c463b
origin/master: dbb7caad17d9d373c4a85a98ddb041db5a6d5e80
Remote: https://github.com/Imase0606/mini-openclaw.git
```

The local worktree intentionally did not merge the three commits after `5a34ede`. It selectively ports the safe `stdout=None` completion fix. Filesystem MCP was removed from runtime startup because the project uses built-in file tools; the upstream `tools/mcp_client.py` POSIX path rewrite was not ported.

Important recent commits:

- `d2ae5c0`: multimodal video knowledge workflow, deployment support, tests, and documentation.
- `c791905`: teammate activity-status improvements.
- `841dd13`: merge of the two development lines.
- `5a34ede`: keep TUI activity status below tool cards.
- `91c3889`: upstream `stdout=None` completion fix, selectively ported.
- `dee3fd0` / `dbb7caa`: upstream MCP path work, reviewed but not ported; filesystem MCP is no longer started by this project.

## Implemented Capabilities

- Bilibili public-video metadata, multipart handling, anonymous/authenticated subtitles, and user-confirmed faster-whisper ASR.
- Simplified-Chinese normalization through OpenCC and one directory per video under `knowledge_base/<BV>/`.
- Duration-aware visual probing with 12-24 representative frames, full-timeline buckets, scene/uniform sampling, perceptual deduplication, and a preview-only contact sheet. MiMo receives up to six individual frames per request.
- MiMo V2.5-first visual analysis with EasyOCR as an optional per-batch fallback.
- Human-readable `index.md`, timestamped transcripts, `visual_notes.jsonl`, and RAG-ready `chunks.jsonl`.
- Personal video RAG, catalog/search, soft delete, restore, export, purge, and rebuildable SQLite indexing.
- Strict tool-argument JSON diagnostics, required-field validation, bounded retries, and explicit error states. `kb_write` now preserves string arrays as Markdown lists, rejects mixed types explicitly, and gives malformed JSON a targeted single-call retry; the TUI marks recoverable failures as `[retry]`, then `[recovered]` after success.
- Textual TUI with image input, session recovery, Todo, permissions, trace/cost views, in-terminal artifact previews, response copying, responsive layouts, and the new `VIDEO + KB` logo.
- Ephemeral Bilibili authentication for course deployment; credentials stay in Runtime memory and never enter the workspace or trace.
- Local echo/calc MCP tools remain available, but their startup status is no longer shown in the TUI. Filesystem access uses the built-in file tools instead of filesystem MCP.

## Runtime Configuration

Required model configuration:

```bash
export DEEPSEEK_API_KEY="..."
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-chat"
```

Recommended visual configuration:

```bash
export VISION_API_KEY="..."
export VISION_BASE_URL="https://api.xiaomimimo.com/v1"
export VISION_MODEL="mimo-v2.5"
```

Do not place keys in repository files. The course `Dockerfile` sets:

```text
BILIBILI_AUTH_MODE=ephemeral
FASTER_WHISPER_MODEL_PATH=/app/models/faster-whisper-base
```

EasyOCR is optional and installed separately with `requirements-ocr.txt`. The main deployment does not need PyTorch when MiMo is configured.

## Usage And Documentation

Primary documents:

- `短视频Claw使用文档.md`: concise course website and end-user guide, version `2026-07-14`.
- `docs/user_guide.md`: complete local development and operation guide.
- `docs/teacher_acceptance.md`: B1/B2/B3/B4 acceptance procedure.
- `docs/demo_checklist.md`: final course release checklist.

Typical commands:

```bash
cd /mnt/d/develop/aiFrontierPractice/mini-openclaw
conda activate openclaw
mini-openclaw
python -m agent.cli --selfcheck
python -m agent.cli "提炼这个视频：https://www.bilibili.com/video/BV.../"
python -m agent.cli --image /path/to/image.png "分析这张图片"
```

## Verification Record

Final checks completed on 2026-07-14:

- `python -m unittest discover -s tests`: **167 tests passed**.
- `python -m eval.demo_check --release`: **25/25 passed**.
- `python -m agent.cli --selfcheck`: passed.
- `python -m compileall -q agent backend tools tui security eval`: passed.
- `pip check`: no broken requirements.
- Wide, narrow, and 60-column compact TUI layouts were snapshot-tested.
- Local `master` and `origin/master` matched at `5a34ede` after that push; `origin/master` later advanced to `dbb7caa` and the current worktree deliberately remains on `5a34ede` with selected safe changes.

Run validation from WSL `openclaw`; the default Windows Python does not contain the project dependencies.

## Deployment Artifact

Current local artifact:

```text
mini-openclaw-deploy-20260714-linux.zip
size: 133,606,982 bytes (127.42 MiB)
SHA256: 8AAD1690C058C378B49DFDA50F2285530F1882FD5227D52F45F9D572938FB1D1
```

It includes source, documentation, and the offline `faster-whisper-base` model. It excludes Git data, API keys, `.mini-openclaw/`, generated knowledge bases, caches, and previous ZIP files.

Important: this ZIP was generated after the npx/logo fixes but before merging teammate commit `c791905` and follow-up `5a34ede`. It remains usable, but a deployment that must exactly match current `master` should regenerate and re-audit the archive in the next session.

## Repository Hygiene

- `*.zip`, `models/`, `.mini-openclaw/`, exports, caches, and runtime-generated knowledge bases are ignored.
- The curated tracked sample under `knowledge_base/BV1j9MP6wEV9/` is the only knowledge-base exception.
- Never commit Bilibili cookies, API keys, local OCR/ASR model downloads, traces, sessions, or generated media.
- Before pushing, fetch `origin/master`, merge normally, rerun tests, and never force-push shared `master`.

## Next Session

There are no known blocking code defects. Start by reading this file and checking `git status` plus `git fetch origin`.

Recommended next action only if another website deployment is required:

1. Regenerate the Linux deployment ZIP from current `master`.
2. Verify the bundled Whisper model, latest source, new logo, and silent npx fallback.
3. Scan the archive for credentials and forbidden runtime paths.
4. Upload it and run `python -m agent.cli --selfcheck` in the platform terminal.

The deployment ZIP and local runtime artifacts are intentionally ignored and should remain outside Git.
