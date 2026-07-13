# Project Handover

## Current Status

mini-openclaw is a Python 3.11 CLI/TUI agent focused on extracting Bilibili public videos into Markdown knowledge bases. Day1-Day10 course capabilities are implemented: ReAct loop, tools, MCP, Skills, multimodal input, security policy and bubblewrap, memory, Todo planning, tracing, evaluation, and a Claude Code-style Textual TUI.

The authenticated Bilibili subtitle path, user-confirmed ASR fallback, personal video RAG, and knowledge-governance flow are implemented. The current uncommitted phase strengthens the negotiated teacher acceptance plan with live link discovery for A/B1/B2 and deterministic fixtures for B3/B4.

Course site: `http://10.130.130.9:10000/index.html`.

## Canonical Working Directory

Use this repository for subsequent work:

```text
D:\develop\aiFrontierPractice\mini-openclaw
branch: master
HEAD: ba94212
WSL path: /mnt/d/develop/aiFrontierPractice/mini-openclaw
Conda Python: /home/imase/miniconda3/envs/openclaw/bin/python
```

A Codex worktree also exists at `C:\Users\HP\.codex\worktrees\bc5e\mini-openclaw` on branch `codex/demo-day-readiness`. Source fixes are mirrored there, but it does **not** contain the ignored bundled Whisper model. Do not package deployments from that worktree.

## Important Uncommitted Changes

- `eval/teacher_acceptance.py` adds `--fresh-live`, strict B1/B2 gates, persistent evidence, and standalone B3 artifacts.
- A/B1/B2 candidates are fetched from current Bilibili feeds and confirmed at runtime. No demonstration BV is hard-coded as the fresh-live result.
- B3 uses empty, very short, and repetitive transcript fixtures; B4 injects hostile text through metadata, transcript, and OCR data.
- `.dockerignore` excludes Git state, runtime knowledge, credentials-adjacent state, caches, and ZIPs while retaining the bundled offline Whisper model.
- Teacher/demo/user documentation and focused acceptance/packaging tests describe the same workflow.

Runtime evidence under `.mini-openclaw/`, generated knowledge bases, ZIP files, models, login state, and Python caches remain ignored and must not be committed.

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
- Current complete suite: 134 tests passed.
- Packaging/model targeted tests: 4 passed.
- TUI resilience suite after the selection fix: 11 passed.
- Teacher deterministic acceptance passes 5/5; red team passes 7/7.
- The current RAG fixture scores Recall@6 1.0, MRR 0.9792, nDCG@6 0.9846, and no-answer accuracy 1.0; 10k chunk p95 is about 278 ms in WSL.
- Run commands from the WSL `openclaw` environment. The Windows default `python` on this machine does not contain project dependencies such as `httpx`.

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

The initial seven-video RAG verification produced 95 timestamp-aware chunks, Hit@6 1.0, MRR 0.7778, no-answer accuracy 1.0, and 88.51% fewer injected characters than loading every transcript. Those 83-test and 18/18 Demo Day figures were historical baselines; use the current verification record below for release decisions.

RAG 2.0 adds schema-versioned content hashes, SimHash duplicate hints, confidence filtering, per-video result caps and lightweight MMR. Knowledge governance supports confirmed soft-delete/restore/export/permanent purge through `personal-video-knowledge-manager`; generated exports and trash remain ignored. The committed 30-query fixture currently scores Recall@6 1.0, MRR 0.9792, nDCG@6 0.9846 and no-answer accuracy 1.0; the 10k chunk benchmark is below the 500ms p95 target in WSL.

## Teacher Acceptance And Content Reliability

- `python -m eval.teacher_acceptance` passes 5/5 deterministic cases: subtitle priority, ASR fallback, insufficient content, prompt injection and restricted vision OCR.
- `python -m eval.teacher_acceptance --fresh-live` fetches and confirms current B1/B2 links instead of accepting source-code or documentation constants. It writes a timestamped report beneath `.mini-openclaw/teacher_acceptance/`.
- The strict B1 gate requires valid authentication, an authenticated Bilibili subtitle source, zero ASR calls, sufficient transcript content, non-empty chunks, `index.md`, and a successful SQLite index update.
- The strict B2 gate requires two subtitle audits, an `allow_asr=false` preflight returning `asr_confirmation_required`, explicit consent, a final ASR source, sufficient content, and a successful index update. Network, authentication, and generic API errors cannot qualify as no-subtitle evidence.
- B3 is deterministic because suitable public videos are not guaranteed: empty, very short, and repetitive transcripts must all create diagnostic entries with zero chunks, `indexed=false`, no retrieval hit, and no model-supplied fabricated summary.
- The live run on `BV1DDjL63ESB` passed from a temporary empty workspace: no public subtitle, local Whisper source, 69 segments, 7 chunks, `content_status=sufficient` and `indexed=true`.
- Insufficient transcripts now produce a fixed diagnostic `index.md`, zero chunks and `indexed=false`; model-supplied summaries are ignored and the entry cannot appear in `kb_search`.
- OCR prefers EasyOCR and falls back to at most six compressed frames through the configured vision model. If neither backend exists, it reports an explicit downgrade.
- The controlled Skill ablation ran three scenarios per group. Both groups had 100% no-content accuracy and zero unsupported-claim proxy hits; Skill improved source traceability from 33.3% to 100% and diagnostic persistence from 0% to 100%, with about 10.5 seconds additional mean latency.
- Current verification: 134/134 unit tests, redteam 7/7, teacher offline 5/5, and fresh-live teacher acceptance 7/7. In the present shell, the default Demo check passes 19/21 and the release gate passes 22/25; only missing DeepSeek/MiMo environment configuration and, for release, the expected dirty worktree fail.

Generic prompts previously crashed only in the deployed editable install because `from mcp.client import MCPClient` resolved to the third-party MCP SDK instead of this project's client. The client now lives at `tools/mcp_client.py`, all imports use that unambiguous namespace, and Runtime resolves bundled MCP server paths absolutely. A regression test starts the installed project from a temporary directory and verifies MCP registration before a generic turn.

Fresh-live evidence on 2026-07-13 passed 7/7 in about 129 seconds and is stored locally at `.mini-openclaw/fresh-live-current/report.json`:

- B1 `BV1DXNC6sEzz`: `subtitle:bilibili:authenticated:ai-zh`, 31 segments, ASR calls 0, 1 chunk, indexed.
- B2 `BV1MxNm6bEL9`: real ASR, 73 segments, sufficient content, 2 chunks, indexed.

B2 discovery is inherently time-sensitive: Bilibili generated a complete AI subtitle for that B2 video about three minutes later. A candidate marked `易变-需立即运行` must be processed immediately. If the final source has changed to a subtitle, discard it and rerun `--fresh-live`; never count it as an ASR pass.

## Bilibili Authenticated Subtitles

- The original anonymous-only yt-dlp path did not access common Bilibili built-in/AI subtitles; all seven cached videos used ASR. Do not describe the previous fixture as a real Bilibili subtitle test.
- Personal local runs default to persistent login through `python -m tools.bilibili_auth login`. The course Docker sets `BILIBILI_AUTH_MODE=ephemeral`; use TUI `/bilibili-login` or `agent.cli --bilibili-login` so QR login and extraction share one process.
- Each Runtime owns a locked in-memory Cookie session for at most 30 minutes. It is cleared by `/new`, logout, close, expiry or failure and never enters keyring, files, workspace, trace, metadata or exports. A shared attachment to the exact same terminal is not a separate security principal; use disabled mode if the platform cannot isolate terminal sessions.
- Subtitle order is anonymous player API, authenticated player API, yt-dlp subtitle fallback, then user-confirmed ASR. `allow_asr=true` is a confirm action.
- `python -m tools.bilibili_subtitles audit` reports anonymous/authenticated availability without modifying knowledge.
- Authenticated subtitle fixtures and the real `--subtitle-auth-live` gate both pass. On 2026-07-13, `BV1Sgjo6SEqg` passed three consecutive authenticated runs with `auth_status=valid`, `subtitle_status=authenticated_found`, `source=subtitle:bilibili:authenticated:ai-zh`, and `allow_asr=false`.
- A live Bilibili popular-feed sample audited eight newly discovered videos: four returned duration-consistent authenticated subtitles and four incomplete/mismatched responses were rejected. Full `allow_asr=false` acceptance passed for `BV1cMNM6BELJ`, `BV1beM76pEBL`, `BV1qLNg66ExY`, and `BV1TgNT67EE5`; the previously intermittent `BV1qLNg66ExY` then passed three consecutive runs after bounded sampling was raised to ten attempts with early exit.

## Next Actions

1. Review and commit only the source, tests, documentation, `.gitignore`, and `.dockerignore` changes from this acceptance phase.
2. Do not commit ZIP files, `models/`, `.playwright-cli/`, API keys, traces, login state, generated knowledge bases, or `.mini-openclaw/` evidence.
3. Before the classroom run, activate WSL `openclaw`, verify Bilibili login status and local Whisper/ffmpeg/yt-dlp availability, then run `--fresh-live`.
4. Run B2 immediately after discovery and before B1 because a newly generated Bilibili AI subtitle invalidates an ASR candidate.
5. Configure `DEEPSEEK_API_KEY` and the optional MiMo visual model for a fully green release gate; the current code-only release check otherwise fails those environment checks plus the expected dirty-worktree check.

No commit or push was performed in this final deployment-fix phase.
