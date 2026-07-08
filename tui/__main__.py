"""TUI 入口：python -m tui"""

from tui.app import MiniOpenClawApp


def main() -> None:
    app = MiniOpenClawApp()
    app.run()


if __name__ == "__main__":
    main()
