# Project Handover

## Current Status

mini-openclaw is a Python 3.11 CLI/TUI agent focused on extracting Bilibili public videos into Markdown knowledge bases. Day1-Day10 course capabilities are implemented: ReAct loop, tools, MCP, Skills, multimodal input, security policy and bubblewrap, memory, Todo planning, tracing, evaluation, and a Claude Code-style Textual TUI.

The latest course-platform deployment eventually built successfully after retrying a transient Hugging Face TLS failure, and a real video extraction task completed. The most recent TUI crash was diagnosed and fixed locally but has not been committed or pushed.

Course site: `http://10.130.130.9:10000/index.html`.

## Canonical Working Directory

Use this repository for subsequent work:

```text
D:\develop\aiFrontierPractice\mini-openclaw
branch: master
HEAD: 1c4f2b4
WSL path: /mnt/d/develop/aiFrontierPractice/mini-openclaw
Conda Python: /home/imase/miniconda3/envs/openclaw/bin/python
```

A Codex worktree also exists at `C:\Users\HP\.codex\worktrees\bc5e\mini-openclaw` on branch `codex/demo-day-readiness`. Source fixes are mirrored there, but it does **not** contain the ignored bundled Whisper model. Do not package deployments from that worktree.

## Important Uncommitted Changes

- `requirements.txt` now contains `-e .`; dependencies moved to `pyproject.toml`. This ensures platform installation registers the `mini-openclaw` console command.
- `requirements-ocr.txt` contains optional CPU-only Torch/EasyOCR dependencies. Main deployment avoids Torch/CUDA.
- `tools/video.py` supports `FASTER_WHISPER_MODEL_PATH` and validates an explicit local model directory.
- `Dockerfile` currently expects a bundled model at `/app/models/faster-whisper-base` and performs no Hugging Face download.
- `tui/app.py` sets `ALLOW_SELECT = False`. This avoids a Textual 8.2.8 crash when clicking a Markdown paragraph replaced during streaming. Prompt/TextArea selection still works; terminal text can be copied with Shift+drag.
- Tests cover package registration, local Whisper selection, malformed TUI output, worker termination, and disabled arbitrary selection.
- `.gitignore` now excludes `build/` and `models/`.

Existing user edits in `MEMORY.md`, defense/demo documents, and other dirty files predate these deployment fixes. Do not revert them.

## Deployment Artifact

Latest offline artifact:

```text
D:\develop\aiFrontierPractice\mini-openclaw\agents-fixed.zip
size: 133,519,168 bytes
SHA256: E8619ECC9A45C81966D3AF353CDCE402AD41D37364D59573D5B30A20758C5A2A
```

It contains `Dockerfile`, `requirements.txt`, source code, and a complete `models/faster-whisper-base/model.bin` (145,217,532 bytes). It excludes Git data, private runtime state, knowledge-base outputs, and Python caches.

The currently successful platform deployment likely used the earlier network-download Dockerfile after a retry. It may remain in use. The offline ZIP is the deterministic fallback for future rebuilds.

After deployment, verify:

```bash
command -v mini-openclaw
echo "$FASTER_WHISPER_MODEL_PATH"
du -sh "$FASTER_WHISPER_MODEL_PATH"
test -s "$FASTER_WHISPER_MODEL_PATH/model.bin" && echo "ASR model OK"
mini-openclaw
```

## Verification Record

Completed successfully during this session:

- Platform-equivalent Aliyun dependency dry-run with no EasyOCR/Torch/CUDA.
- Isolated package installation generated the `mini-openclaw` console script.
- Offline `faster-whisper-base` load succeeded with `HF_HUB_OFFLINE=1`.
- CLI `--selfcheck` and `compileall` passed.
- Earlier complete suite: 73 tests passed.
- Packaging/model targeted tests: 4 passed.
- TUI resilience suite after the selection fix: 11 passed.
- One full-suite run later hit the known timing-flaky queue test; its isolated rerun passed. Run the full suite again before release.

Commands:

```bash
cd /mnt/d/develop/aiFrontierPractice/mini-openclaw
/home/imase/miniconda3/envs/openclaw/bin/python -m unittest discover -s tests
/home/imase/miniconda3/envs/openclaw/bin/python -m agent.cli --selfcheck
/home/imase/miniconda3/envs/openclaw/bin/python -m eval.demo_check
/home/imase/miniconda3/envs/openclaw/bin/python -m security.redteam
```

## Architecture Pointers

- `agent/runtime.py`: shared CLI/TUI runtime.
- `agent/loop.py`: bounded agent loop, retries, planning and event emission.
- `agent/policy.py`, `agent/permissions.py`: tool permissions and video-task minimum privilege.
- `tools/video.py`: metadata, subtitle/ASR, OCR and knowledge-base writing.
- `skills/video-summary/SKILL.md`: video extraction orchestration.
- `tui/screens.py`, `tui/chat_view.py`: TUI lifecycle and streamed Markdown.
- `agent/tracer.py`: JSONL traces under ignored `.mini-openclaw/`.
- `tests/test_tui_resilience.py`: prior TUI crash regressions.

## Personal Video Knowledge RAG

The workspace now includes a lightweight, local RAG layer over accumulated video extractions:

- `tools/knowledge.py` builds timestamp-aware chunks and a rebuildable SQLite index at `.mini-openclaw/video_knowledge.sqlite3`.
- `kb_write` incrementally indexes each completed video; `python -m tools.knowledge --reindex` imports existing knowledge bases without redownloading media or running ASR.
- `kb_search` and `kb_catalog` are read-only tools. The `personal-video-knowledge` Skill answers from prior videos first and places model knowledge in a separately labeled supplement.
- Knowledge QA uses a least-privilege policy and cannot download videos, write files, execute Shell, or start MCP.
- `python -m eval.rag_evaluation --reindex` reports Hit@K, MRR, no-answer accuracy, and context reduction.

Verification on the current seven-video cache produced 95 timestamp-aware chunks, Hit@6 1.0, MRR 0.7778, no-answer accuracy 1.0, and 88.51% fewer injected characters than loading every transcript. The complete suite passes with 83 tests; the offline Demo Day check passes 18/18.

RAG 2.0 adds schema-versioned content hashes, SimHash duplicate hints, confidence filtering, per-video result caps and lightweight MMR. Knowledge governance supports confirmed soft-delete/restore/export/permanent purge through `personal-video-knowledge-manager`; generated exports and trash remain ignored. The committed 30-query fixture currently scores Recall@6 1.0, MRR 0.9792, nDCG@6 0.9846 and no-answer accuracy 1.0; the 10k chunk benchmark is below the 500ms p95 target in WSL.

## Next Actions

1. Keep the currently successful deployment if video ASR and TUI work; redeploy the offline ZIP only when deterministic rebuilding is needed.
2. Run the full test suite once more.
3. Review the dirty worktree and commit source/documentation changes only. Do not commit ZIP files, `models/`, `.playwright-cli/`, API keys, traces, or generated knowledge bases.
4. Before pushing, decide long-term model distribution. The current Dockerfile requires an ignored bundled model, which is suitable for the course ZIP but not for a clean Git clone. A production solution should use a release artifact, model volume, or explicit build option.
5. Remove or archive obsolete `.agentsv1.zip` only after confirming the latest deployment.

No commit or push was performed in this final deployment-fix phase.
