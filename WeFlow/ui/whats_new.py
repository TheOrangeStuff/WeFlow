""" "What's new": shown once, the first time a newer version starts."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .widgets.common import Button, label

WIDTH = 480
MARGIN = 24
MAX_LIST_HEIGHT = 320


class WhatsNew(QDialog):
    def __init__(self, version: str, items: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("What's new")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(WIDTH)
        self.heading = label(f"What's new in WeFlow {version}", "title")
        self.items = [label(text, "body", wrap=True) for text in items]

        rows = QVBoxLayout()
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(8)
        for item in self.items:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(label("•", "secondary"), 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(item, 1)
            rows.addLayout(row)
        rows.addStretch(1)
        content = QWidget()
        content.setLayout(rows)
        self.list = QScrollArea()
        self.list.setWidgetResizable(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setWidget(content)
        # Short lists show in full; long ones scroll.
        needed = rows.totalHeightForWidth(WIDTH - 2 * MARGIN)
        self.list.setFixedHeight(min(needed, MAX_LIST_HEIGHT))

        self.done_button = Button("Done", "primary")
        self.done_button.setDefault(True)
        self.done_button.clicked.connect(self.accept)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.done_button)

        column = QVBoxLayout(self)
        column.setContentsMargins(MARGIN, MARGIN, MARGIN, 16)
        column.setSpacing(16)
        column.addWidget(self.heading)
        column.addWidget(self.list)
        column.addLayout(footer)
