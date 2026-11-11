"""The working face: one column, state first, controls only when they apply (§7.2)."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .bridge import Batch, Bridge, Profile
from .widgets import ActivityDrawer, Button, CardRow, EventCard, StatusPanel, label, view_for
from .widgets.notice_bar import NoticeBar

MAX_ROWS = 5
ROW_HEIGHT = 44
DONE_FADE_MS = 10_000
NOTICE_MS = 5_000


class MainWindow(QMainWindow):
    notify = Signal(str, str, str)  # kind, title, message  (for the tray)
    status_changed = Signal(str)  # idle | working | safe | attention
    settings_requested = Signal()
    edit_event_requested = Signal(object)  # Profile (a draft when new)
    quit_requested = Signal()

    def __init__(self, bridge: Bridge) -> None:
        super().__init__()
        self.bridge = bridge
        self.setWindowTitle("WeFlow")
        self.resize(560, 720)
        self.setMinimumSize(480, 600)
        self.done_fade_ms = DONE_FADE_MS
        self.can_hide_to_tray = False
        self._batches: dict[int, Batch] = {}
        self._cards: dict[str, str] = {}
        self._waiting_for_event: dict[str, str] = {}
        self._queued_cards: dict[str, str] = {}
        self._dismissed: set[int] = set()
        self._shown_batch: Batch | None = None
        self._rows: dict[int, CardRow] = {}
        self._status = "idle"
        self._focus_cleared = False

        page = QWidget()
        page.setObjectName("Page")
        page.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCentralWidget(page)

        # 1. Top bar: name, Pause/Resume, Settings. Nothing else is always visible.
        self.pause = Button("Pause", "quiet", icon="pause", icon_token="text-2")
        self.pause.clicked.connect(self._toggle_pause)
        self.settings = Button(kind="icon", icon="settings", icon_token="text-2", tip="Settings")
        self.settings.clicked.connect(self.settings_requested)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        top.addWidget(label("WeFlow", "heading"), 1)
        top.addWidget(self.pause)
        top.addWidget(self.settings)
        top_bar = QWidget()
        top_bar.setFixedHeight(48)
        top_bar.setLayout(top)

        # 2. Event card
        self.event_card = EventCard()
        self.event_card.switch_requested.connect(self.bridge.set_active)
        self.event_card.new_requested.connect(self._new_event)
        self.event_card.edit_requested.connect(self._edit_event)
        self.event_card.import_requested.connect(self._import_folder)

        # Card notice (only while a card is waiting to start)
        self.notice_bar = NoticeBar(NOTICE_MS)
        self.notice_bar.started.connect(self._start_card)
        self.notice_bar.cancelled.connect(self._card_cancelled)

        # 3. Status panel
        self.panel = StatusPanel()
        self.panel.eject_clicked.connect(self._eject)
        self.panel.release_clicked.connect(self._release)
        self.panel.retry_clicked.connect(self._retry)
        self.panel.details_clicked.connect(lambda: self.drawer.open_drawer())
        self.panel.open_folder_clicked.connect(self._open_folder)
        self.panel.resume_clicked.connect(lambda: self._set_paused(False))

        # 4. Earlier cards
        self.earlier_label = label("Earlier cards", "label")
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch(1)
        rows = QWidget()
        rows.setLayout(self._rows_layout)
        self.earlier = QScrollArea()
        self.earlier.setWidgetResizable(True)
        self.earlier.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.earlier.setWidget(rows)

        # 5. Activity drawer handle
        self.activity = Button(
            "Activity", "quiet", icon="chevron-right", icon_token="text-2", icon_after=True
        )
        self.activity.clicked.connect(lambda: self.drawer.toggle())
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        bottom.addWidget(self.activity)
        self.bottom_bar = QWidget()
        self.bottom_bar.setFixedHeight(32)
        self.bottom_bar.setLayout(bottom)

        column = QVBoxLayout(page)
        column.setContentsMargins(24, 0, 24, 8)
        column.setSpacing(0)
        column.addWidget(top_bar)
        column.addSpacing(8)
        column.addWidget(self.event_card)
        column.addSpacing(12)
        column.addWidget(self.notice_bar)
        self._notice_gap = QWidget()
        self._notice_gap.setFixedHeight(12)
        column.addWidget(self._notice_gap)
        column.addWidget(self.panel, 1)
        self._list_gap = QWidget()
        self._list_gap.setFixedHeight(24)
        column.addWidget(self._list_gap)
        column.addWidget(self.earlier_label)
        column.addSpacing(4)
        column.addWidget(self.earlier)
        column.addSpacing(8)
        column.addWidget(self.bottom_bar)

        self.drawer = ActivityDrawer(page)

        self._done_timer = QTimer(self)
        self._done_timer.setSingleShot(True)
        self._done_timer.timeout.connect(self._fade_done)

        bridge.batch_updated.connect(self._on_batch)
        bridge.card_detected.connect(self._on_card_detected)
        bridge.card_removed.connect(self._on_card_removed)
        bridge.notice.connect(self._on_notice)
        bridge.events_changed.connect(self._on_events_changed)
        bridge.settings_changed.connect(self.refresh)
        bridge.eject_finished.connect(self._on_eject_finished)

        for batch in bridge.snapshot():
            self._batches[batch.id] = batch
        self._on_events_changed()

    # ----------------------------------------------------------------- state
    def _visible_batches(self) -> list[Batch]:
        """Newest first. Finished rows from earlier app sessions stay hidden."""
        keep = [
            b
            for b in self._batches.values()
            if not (b.previous_session and b.state in ("done", "error"))
        ]
        return sorted(keep, key=lambda b: b.started, reverse=True)

    def refresh(self) -> None:
        active = self.bridge.active()
        paused = self.bridge.settings.paused
        visible = self._visible_batches()
        current = visible[0] if visible and visible[0].id not in self._dismissed else None
        self._shown_batch = current
        view = view_for(
            current,
            event_name=active.name if active else "",
            paused=paused,
            card_present=bool(current and current.source in self._cards),
        )
        self.panel.show_view(view)

        if view.state == "done" and current is not None:
            if not self._done_timer.isActive():
                self._done_timer.start(self.done_fade_ms)
        else:
            self._done_timer.stop()

        self._sync_rows([b for b in visible if current is None or b.id != current.id])
        self.pause.setText("Resume" if paused else "Pause")
        self.pause.set_icon("play" if paused else "pause")
        self.activity.setVisible(bool(self.drawer.entries))
        self._notice_gap.setVisible(self.notice_bar.isVisibleTo(self))

        if any(b.state in ("attention", "error") for b in visible):
            status = "attention"
        elif view.state == "safe":
            status = "safe"
        elif self.bridge.has_pending():
            status = "working"
        else:
            status = "idle"
        if status != self._status:
            self._status = status
            self.status_changed.emit(status)

    def status(self) -> str:
        return self._status

    def _sync_rows(self, batches: list[Batch]) -> None:
        wanted = {b.id for b in batches}
        for batch_id in [i for i in self._rows if i not in wanted]:
            row = self._rows.pop(batch_id)
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        for index, batch in enumerate(batches):
            row = self._rows.get(batch.id)
            if row is None:
                row = CardRow(batch)
                row.action_clicked.connect(self._row_action)
                self._rows[batch.id] = row
            else:
                row.set_batch(batch)
            if self._rows_layout.indexOf(row) != index:
                self._rows_layout.insertWidget(index, row)
        has_rows = bool(batches)
        for widget in (self.earlier_label, self.earlier, self._list_gap):
            widget.setVisible(has_rows)
        self.earlier.setFixedHeight(min(len(batches), MAX_ROWS) * ROW_HEIGHT)

    def rows(self) -> list[CardRow]:
        ordered = [self._rows_layout.itemAt(i).widget() for i in range(self._rows_layout.count())]
        return [w for w in ordered if isinstance(w, CardRow)]

    def _fade_done(self) -> None:
        if self._shown_batch is not None and self._shown_batch.state == "done":
            self._dismissed.add(self._shown_batch.id)
            self.refresh()

    # --------------------------------------------------------- bridge events
    def _on_batch(self, batch: Batch) -> None:
        previous = self._batches.get(batch.id)
        self._batches[batch.id] = batch
        if batch.state == "done" and previous is not None and previous.state != "done":
            self.notify.emit("done", "All done", f"{batch.label}: {self._done_text(batch)}")
        self.refresh()

    def _done_text(self, batch: Batch) -> str:
        return view_for(batch).subline

    def _on_card_detected(self, path: str, card_label: str) -> None:
        self._cards[path] = card_label
        if self.bridge.settings.card_sound:
            QApplication.beep()
        active = self.bridge.active()
        if active is None:
            # FR-11: with no event, ask for one; the card starts once it is chosen.
            self._waiting_for_event[path] = card_label
            self.notify.emit("card", f"Card {card_label}", "Choose an event to start.")
            self.event_card.open_switcher()
        else:
            self.notify.emit("card", f"Card {card_label}", f"Copying to {active.name} soon.")
            self._announce(path, card_label, active)
        self.refresh()

    def _announce(self, path: str, card_label: str, event: Profile) -> None:
        if self.notice_bar.card() is None:
            self.notice_bar.show_card(path, card_label, event.name)
        else:
            self._queued_cards[path] = card_label

    def _next_queued(self) -> None:
        active = self.bridge.active()
        if self._queued_cards and active is not None and self.notice_bar.card() is None:
            path = next(iter(self._queued_cards))
            self.notice_bar.show_card(path, self._queued_cards.pop(path), active.name)

    def _start_card(self, path: str, card_label: str) -> None:
        if path in self._cards:
            self.bridge.submit(path, card_label, True)
        self._next_queued()
        self.refresh()

    def _card_cancelled(self, path: str) -> None:
        self.drawer.add("info", f"{self._cards.get(path, 'Card')}: import cancelled.")
        self._next_queued()
        self.refresh()

    def _on_card_removed(self, path: str) -> None:
        self._cards.pop(path, None)
        self._waiting_for_event.pop(path, None)
        self._queued_cards.pop(path, None)
        self.notice_bar.dismiss_card(path)
        self._next_queued()
        self.refresh()

    def _on_notice(self, level: str, message: str, batch_id: object) -> None:
        self.drawer.add(level, message)
        if level == "safe":
            self.notify.emit("safe", "Safe to remove", message)
        elif level == "error":
            self.notify.emit("attention", "Needs attention", message)
        self.refresh()

    def _on_events_changed(self) -> None:
        active = self.bridge.active()
        self.event_card.set_events(self.bridge.events(), active)
        if active is not None and self._waiting_for_event:
            waiting, self._waiting_for_event = self._waiting_for_event, {}
            for path, card_label in waiting.items():
                if path in self._cards:
                    self._announce(path, card_label, active)
        self.refresh()

    def _on_eject_finished(self, path: str, ok: bool, message: str) -> None:
        self.panel.set_ejecting(False)
        if ok:
            self._cards.pop(path, None)
            self.notice_bar.show_message("safe", "Card ejected. You can take it out.")
            self.drawer.add("info", "Card ejected.")
        else:
            self.notice_bar.show_message("error", message)
            self.drawer.add("error", message)
        self.refresh()

    # --------------------------------------------------------------- actions
    def _toggle_pause(self) -> None:
        self._set_paused(not self.bridge.settings.paused)

    def _set_paused(self, paused: bool) -> None:
        if paused:
            for path in list(self._cards):
                self.notice_bar.dismiss_card(path)
            self._cards.clear()
            self._queued_cards.clear()
            self._waiting_for_event.clear()
        self.bridge.set_paused(paused)

    def set_paused(self, paused: bool) -> None:
        self._set_paused(paused)

    def _eject(self) -> None:
        if self._shown_batch is not None:
            self.panel.set_ejecting(True)
            self.bridge.eject(self._shown_batch.source)

    def _release(self) -> None:
        if self._shown_batch is not None:
            self.bridge.release(self._shown_batch.event_id)

    def _retry(self) -> None:
        batch = self._shown_batch
        if batch is None:
            return
        if batch.state == "error":  # copy failed: run the copy again
            self.bridge.submit(batch.source, batch.label, batch.is_card, batch.event_id)
        else:
            self.bridge.retry(batch.event_id)

    def _open_folder(self) -> None:
        if self._shown_batch is not None:
            self.bridge.open_folder(self.bridge.event_folder(self._shown_batch.event_id))

    def _row_action(self, name: str, batch: Batch) -> None:
        if name == "retry":
            self.bridge.retry(batch.event_id)
        elif name == "release":
            self.bridge.release(batch.event_id)
        elif name == "open":
            self.bridge.open_folder(self.bridge.event_folder(batch.event_id))

    def _new_event(self) -> None:
        self.edit_event_requested.emit(self.bridge.draft_event())

    def _edit_event(self) -> None:
        active = self.bridge.active()
        if active is not None:
            self.edit_event_requested.emit(active)

    def _import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import folder")
        if folder:
            self.import_folder(folder)

    def import_folder(self, folder: str) -> None:
        """FR-18: run the same pipeline on any folder."""
        self.bridge.submit(folder, os.path.basename(folder.rstrip("/\\")) or folder, False)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._focus_cleared:  # no control starts out focused; Tab enters the top bar
            self._focus_cleared = True
            self.centralWidget().setFocus()

    # ----------------------------------------------------------------- close
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.can_hide_to_tray and self.bridge.has_pending():
            event.ignore()
            self.hide()
            self.notify.emit(
                "background", "WeFlow is still working", "It will finish in the background."
            )
        else:
            event.accept()
            self.quit_requested.emit()
