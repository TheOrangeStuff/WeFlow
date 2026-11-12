"""Status chip: a tinted pill that always pairs its colour with an icon and a word."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy

from ..bridge import Batch
from .common import Icon, label, restyle

# batch state -> (tone, icon, word)
BATCH_CHIPS = {
    "copying": ("warn", "hourglass", "Copying"),
    "safe": ("warn", "refresh-cw", "Processing"),
    "processing": ("warn", "refresh-cw", "Processing"),
    "uploading": ("warn", "cloud-upload", "Uploading"),
    "ready": ("warn", "clock", "Waiting"),
    "held": ("warn", "clock", "Held"),
    "done": ("good", "check", "Done"),
    "attention": ("bad", "triangle-alert", "Needs attention"),
    "error": ("bad", "triangle-alert", "Copy failed"),
}
ICON_TOKENS = {
    "good": "good-strong",
    "warn": "warn-strong",
    "bad": "bad-strong",
    "neutral": "text-2",
}


class Chip(QFrame):
    def __init__(self, text: str = "", tone: str = "neutral", icon: str = "info") -> None:
        super().__init__()
        self.setObjectName("Chip")
        self.setFixedHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 12, 0)
        row.setSpacing(4)
        self._icon = Icon(icon, ICON_TOKENS[tone], 16)
        self._text = label(text, "label")
        row.addWidget(self._icon)
        row.addWidget(self._text)
        self.tone = ""
        self.set(text, tone, icon)

    def set(self, text: str, tone: str, icon: str) -> None:
        self.tone = tone
        self._text.setText(text)
        self._icon.set_icon(icon, ICON_TOKENS[tone])
        restyle(self, tone=tone)
        restyle(self._text, tone=tone)
        self.setAccessibleName(text)

    def text(self) -> str:
        return self._text.text()

    def set_batch(self, batch: Batch) -> None:
        tone, icon, word = BATCH_CHIPS.get(batch.state, ("neutral", "info", batch.state))
        self.set(word, tone, icon)
