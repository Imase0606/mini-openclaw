"""Reusable selection modal for sessions, models and permission modes."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option


class ChoiceModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, choices: list[tuple[str, str]]) -> None:
        super().__init__()
        self.title_text = title
        self.choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="choice-dialog"):
            yield Label(self.title_text, id="choice-title")
            yield OptionList(
                *[Option(Text(label), id=value) for value, label in self.choices],
                id="choice-options",
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class BilibiliLoginModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, qr_text: str) -> None:
        super().__init__()
        self.qr_text = qr_text

    def compose(self) -> ComposeResult:
        with Vertical(id="bilibili-login-dialog"):
            yield Label("Bilibili subtitle login", id="bilibili-login-title")
            yield Static(self.qr_text, id="bilibili-login-qr", markup=False)
            yield Static(
                "Scan with Bilibili. Login stays in this terminal for at most 30 minutes.",
                id="bilibili-login-status",
                markup=False,
            )
            with Horizontal(id="bilibili-login-actions"):
                yield Button("Close", id="bilibili-login-close")

    def update_status(self, text: str) -> None:
        if self.is_mounted:
            self.query_one("#bilibili-login-status", Static).update(text)

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)
