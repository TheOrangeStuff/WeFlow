"""The event line inside the status panel: "Photos go to <event>" and the way to switch
(UI-01, FR-02)."""

from __future__ import annotations

import re

from PySide6.QtCore import QPoint, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QSizePolicy, QVBoxLayout

from ..bridge import Profile, service_name
from ..theme import theme
from .common import Button, label


def album_display(event: Profile) -> str:
    """A short, human form of the album field (gallery links can be long)."""
    album = event.album_name.strip() or event.album.strip()
    if "://" in album or album.startswith("/"):
        parts = [p for p in re.split(r"[/?#]", album.split("://")[-1])[1:] if p]
        parts = [p for p in parts if not p.startswith("n-") and p not in ("api", "v2", "album")]
        album = parts[-1] if parts else "gallery"
    return album if len(album) <= 28 else album[:27] + "…"


def event_summary(event: Profile) -> str:
    """Line 2 of the card: destination · album · Watermark · Held (only what applies)."""
    if event.destination == "none":
        parts = ["Library only"]
    else:
        parts = [service_name(event.destination)]
        if event.album.strip():
            parts.append(album_display(event))
    if event.watermark:
        parts.append("Watermark")
    if event.hold_uploads and event.destination != "none":
        parts.append("Held")
    return " · ".join(parts)


class EventCard(QFrame):
    switch_requested = Signal(str)  # event id
    new_requested = Signal()
    edit_requested = Signal()
    import_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("EventCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._events: list[Profile] = []
        self._active_id = ""
        self.prefix = label("Photos go to", "body")
        self.name = label("", "heading")
        self.summary = label("", "secondary")
        self.switch = Button(
            "Switch", "text", icon="chevron-down", icon_token="accent", icon_after=True
        )
        self.switch.setAccessibleName("Switch event")
        self.switch.setToolTip("Choose which event new cards go into, or make a new one.")
        self.switch.clicked.connect(self.open_switcher)
        self.menu = QMenu(self)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.prefix)
        row.addWidget(self.name)
        row.addWidget(self.switch)
        row.addStretch(1)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addLayout(row)
        column.addWidget(self.summary)
        self.set_events([], None)

    def set_events(self, events: list[Profile], active: Profile | None) -> None:
        self._events = sorted(events, key=lambda e: e.name.casefold())
        self._active_id = active.id if active else ""
        if active is None:
            self.prefix.hide()
            self.name.setText("No event chosen")
            self.summary.setText("Choose or create an event to begin")
            self.switch.setText("Choose")
        else:
            self.prefix.show()
            self.name.setText(active.name if len(active.name) <= 40 else active.name[:39] + "…")
            self.summary.setText(event_summary(active))
            self.switch.setText("Switch")
        self.name.setToolTip(active.name if active else "")

    def build_menu(self) -> QMenu:
        """The popover: every event, then New event…, Edit this event…, Import folder…"""
        menu = self.menu
        menu.clear()
        for event in self._events:
            action = QAction(event.name, menu)
            if event.id == self._active_id:
                action.setIcon(theme.icon("check", "text", 16))
                action.setIconVisibleInMenu(True)
            action.setData(event.id)
            action.triggered.connect(
                lambda _checked=False, eid=event.id: self.switch_requested.emit(eid)
            )
            menu.addAction(action)
        if self._events:
            menu.addSeparator()
        menu.addAction("New event…", self.new_requested.emit)
        if self._active_id:
            menu.addAction("Edit this event…", self.edit_requested.emit)
            menu.addSeparator()
            menu.addAction("Import folder…", self.import_requested.emit)
        return menu

    def open_switcher(self) -> None:
        menu = self.build_menu()
        menu.setMinimumWidth(240)
        corner = self.switch.mapToGlobal(QPoint(self.switch.width(), self.switch.height() + 4))
        menu.popup(QPoint(corner.x() - max(menu.sizeHint().width(), 240), corner.y()))
