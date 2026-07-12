"""Generate deterministic Textual SVGs for TUI visual review."""
from __future__ import annotations

import asyncio
from pathlib import Path

from agent.runtime import AgentRuntime
from tui.app import MiniOpenClawApp


SIZES = {
    "claude-wide.svg": (120, 40),
    "claude-narrow.svg": (80, 32),
    "claude-compact.svg": (60, 24),
}


def runtime_factory() -> AgentRuntime:
    return AgentRuntime(trace_enabled=False, enable_mcp=False)


async def generate(output: Path = Path(".mini-openclaw")) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for filename, size in SIZES.items():
        app = MiniOpenClawApp(runtime_factory)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screenshot = Path(app.save_screenshot(filename=filename, path=output))
            paths.append(screenshot)
    return paths


def main() -> int:
    for path in asyncio.run(generate()):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
