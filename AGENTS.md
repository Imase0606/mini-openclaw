# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python starter implementation of a command-line agent. Core orchestration lives in `agent/`, with `agent/cli.py` as the CLI entry point and `agent/loop.py` for the ReAct-style loop. Model access is in `backend/`; prompt rendering and tool-call parsing are in `prompt/`. Built-in tools are under `tools/`, MCP examples are under `mcp/`, and loadable skills live in `skills/`. The Textual UI is in `tui/`, including `styles.tcss` and `tui/scared.png`. Evaluation helpers are in `eval/`. There is currently no dedicated `tests/` directory.

## Build, Test, and Development Commands

- `python -m venv .venv` then `.venv\Scripts\Activate.ps1`: create and activate a local virtual environment on Windows.
- `pip install -r requirements.txt`: install runtime dependencies.
- `python -m agent.cli --selfcheck`: run the built-in skeleton health check.
- `python -m agent.cli "your task"`: run the agent against a natural-language task. Set `DEEPSEEK_API_KEY` for the real backend; otherwise it falls back to `FakeBackend`.
- `python -m tui`: launch the Textual terminal UI.
- `python -m eval.ablation`: run the sample evaluation comparison.

## Coding Style & Naming Conventions

Use Python 3.11+ features consistently with the existing code. Prefer 4-space indentation, type hints for public functions, and small modules grouped by responsibility. Use `snake_case` for functions, variables, and module names; `PascalCase` for classes; and uppercase names for constants. Keep comments short and practical, especially around TODO milestones such as `# TODO[DayN]`. Avoid broad rewrites when completing a day-specific task.

## Testing Guidelines

No formal test framework is configured yet. For now, validate changes with `python -m agent.cli --selfcheck` plus the most relevant module command, such as `python -m eval.ablation` or a focused agent task. If adding tests, create a `tests/` directory, use `pytest`, name files `test_*.py`, and keep fixtures small enough to run without external services.

## Commit & Pull Request Guidelines

Recent commits use short milestone-oriented messages, such as `day3_add` or `Day4 ...`. Keep messages concise and mention the day/module affected when useful. Pull requests should include a short summary, verification commands run, linked issue or milestone if applicable, and screenshots or terminal output for TUI/CLI changes. Note any required environment variables, especially `DEEPSEEK_API_KEY` and `MCP_FS_DIR`.

## Security & Configuration Tips

Do not commit API keys, local model files, virtual environments, or generated binaries. Scope `MCP_FS_DIR` to the smallest directory needed when enabling the filesystem MCP server.
