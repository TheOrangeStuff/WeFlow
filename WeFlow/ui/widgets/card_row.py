"""One row of the "Earlier cards" list (UI-04, UI-06)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy

from ..bridge import Batch, plural
from .chip import Chip
from .common import Button, label

# batch state -> (button text, action name); other states have no action
ACTIONS = {
    "attention": ("Retry", "retry"),
    "ready": ("Process", "process"),
    "held": ("Upload", "release"),
    "done": ("Open folder", "open"),
}


def row_text(batch: Batch) -> str:
    count = batch.copied if batch.state != "copying" else batch.total
    return f"{batch.label} · {plural(count)}"


class CardRow(QFrame):
    action_clicked = Signal(str, object)  # action name, Batch

    def __init__(self, batch: Batch) -> None:
        super().__init__()
        self.batch = batch
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.text = label("", "body")
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.action = Button("", "text")
        self.action.clicked.connect(self._clicked)
        self.chip = Chip()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.text, 1)
        row.addWidget(self.action)
        row.addWidget(self.chip)
        self.set_batch(batch)

    def set_batch(self, batch: Batch) -> None:
        self.batch = batch
        self.text.setText(row_text(batch))
        self.chip.set_batch(batch)
        text, _name = ACTIONS.get(batch.state, ("", ""))
        self.action.setText(text)
        self.action.setVisible(bool(text))
        self.setToolTip(batch.error if batch.state in ("attention", "error") else batch.event_name)

    def action_name(self) -> str:
        return ACTIONS.get(self.batch.state, ("", ""))[1]

    def _clicked(self) -> None:
        if self.action_name():
            self.action_clicked.emit(self.action_name(), self.batch)
