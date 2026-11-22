"""The working face: state first, controls only when they apply (§7.2).

1.14.0: top bar (brand, Speed, Auto/Paced, Pause, Settings), the upload destination,
a hero with the big headline over the newest photo, and the three stages along the
bottom.
"""

from __future__ import annotations

import os
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QImage, QShowEvent
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
from .widgets import (
    ActivityDrawer,
    Button,
    CardRow,
    EventCard,
    ModeSwitch,
    StageView,
    StatusPanel,
    label,
    stages_for,
    view_for,
)
from .widgets.common import separator
from .widgets.notice_bar import NoticeBar
from .widgets.speed_meter import SpeedControl

MAX_ROWS = 2  # 1.14.0: the hero needs the room; the list scrolls after 2
ROW_HEIGHT = 44
DONE_FADE_MS = 10_000
NOTICE_MS = 15_000  # 1.15.0: was 5 s (the owner asked)
PHOTO_EDGE = 1600  # longest side of the hero photo
SIZE = (960, 720)
MINIMUM = (760, 640)


PAUSE_TIPS = {
    False: "Stop watching for new cards. Photos already copied keep processing and uploading.",
    True: "Start watching for new cards again.",
}


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
        self.resize(*SIZE)
        self.setMinimumSize(*MINIMUM)
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
        self._update_opened = ""  # version whose download page was opened this session
        self._ejected: set[int] = set()  # batches whose card WeFlow ejected
        self._auto_ejecting: set[int] = set()  # batches auto-eject has been tried for
        self._ejecting: dict[str, int] = {}  # card path -> batch, while an eject runs
        self._erasing: dict[int, str] = {}  # batch -> card path, while an erase runs
        self._photo_key: tuple[object, ...] = ()  # what the hero photo was last loaded for

        page = QWidget()
        page.setObjectName("Page")
        page.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCentralWidget(page)

        # 1. Top bar: brand; Speed; Auto/Paced, Pause/Resume, Settings (UI-05, widened
        #    for the mode switch and, in 1.14.0, Speed: DECISIONS).
        self.speed = SpeedControl(bridge.settings.speed)
        self.speed.changed.connect(self.bridge.set_speed)
        self.mode = ModeSwitch(bridge.settings.paced)
        self.mode.toggled.connect(self.bridge.set_paced)
        self.pause = Button("Pause", "quiet", icon="pause", icon_token="text-2")
        self.pause.clicked.connect(self._toggle_pause)
        self.settings = Button(kind="icon", icon="settings", icon_token="text", tip="Settings")
        self.settings.setIconSize(QSize(22, 22))
        self.settings.clicked.connect(self.settings_requested)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        brand.addWidget(label("WF", "brand-mark"))
        brand.addWidget(label("WeFlow", "brand"))
        top = QHBoxLayout()
        top.setContentsMargins(12, 0, 0, 0)
        top.setSpacing(16)
        top.addLayout(brand)
        top.addStretch(1)
        top.addWidget(self.speed)
        top.addStretch(1)
        top.addWidget(self.mode)
        top.addWidget(self.pause)
        top.addWidget(self.settings)
        top_bar = QWidget()
        top_bar.setFixedHeight(80)
        top_bar.setLayout(top)

        # 2. Upload destination: "<event> / <where>" and Switch
        self.event_card = EventCard()
        self.event_card.switch_requested.connect(self.bridge.set_active)
        self.event_card.new_requested.connect(self._new_event)
        self.event_card.edit_requested.connect(self._edit_event)
        self.event_card.import_requested.connect(self._import_folder)

        # Card notice (only while a card is waiting to start)
        self.notice_bar = NoticeBar(NOTICE_MS)
        self.notice_bar.started.connect(self._start_card)
        self.notice_bar.cancelled.connect(self._card_cancelled)
        self.notice_bar.download_clicked.connect(self._download_update)
        self.notice_bar.skipped.connect(self.bridge.skip_update)

        # 3. Status panel
        self.panel = StatusPanel()
        self.panel.eject_clicked.connect(self._eject)
        self.panel.erase_clicked.connect(self._erase)
        self.panel.release_clicked.connect(self._release)
        self.panel.process_clicked.connect(self._process)
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
        self.activity.setToolTip("Show what WeFlow has done this session.")
        self.activity.clicked.connect(lambda: self.drawer.toggle())
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        bottom.addWidget(self.activity)
        self.bottom_bar = QWidget()
        self.bottom_bar.setFixedHeight(32)
        self.bottom_bar.setLayout(bottom)

        column = QVBoxLayout(page)
        column.setContentsMargins(32, 0, 32, 8)
        column.setSpacing(0)
        column.addWidget(top_bar)
        column.addWidget(separator())
        column.addWidget(self.event_card)
        column.addWidget(separator())
        self._notice_gap = QWidget()
        self._notice_gap.setFixedHeight(12)
        column.addWidget(self._notice_gap)
        column.addWidget(self.notice_bar)
        column.addWidget(self.panel, 1)
        self._list_gap = QWidget()
        self._list_gap.setFixedHeight(16)
        column.addWidget(self._list_gap)
        column.addWidget(self.earlier_label)
        column.addSpacing(4)
        column.addWidget(self.earlier)
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
        bridge.erase_finished.connect(self._on_erase_finished)
        bridge.update_changed.connect(self.refresh)
        bridge.update_install_changed.connect(self._on_update_install)
        bridge.quit_for_update.connect(self.quit_requested)

        for batch in bridge.snapshot():
            self._batches[batch.id] = batch
        self._on_events_changed()

    # ----------------------------------------------------------------- state
    def _visible_batches(self) -> list[Batch]:
        """Newest first. Finished rows from earlier app sessions stay hidden, and of the
        finished batches the camera sent (FR-48) only the newest shows."""
        keep = [
            b
            for b in self._batches.values()
            if not (b.previous_session and b.state in ("done", "error"))
        ]
        keep.sort(key=lambda b: b.started, reverse=True)
        newest_live = next((b.id for b in keep if b.live), None)
        return [b for b in keep if not (b.live and b.state == "done" and b.id != newest_live)]

    def refresh(self) -> None:
        active = self.bridge.active()
        paused = self.bridge.settings.paused
        visible = self._visible_batches()
        current = visible[0] if visible and visible[0].id not in self._dismissed else None
        self._shown_batch = current
        card_in = bool(current and current.source in self._cards)
        view = view_for(
            current,
            event_name=active.name if active else "",
            paused=paused,
            card_present=card_in,
            watching=self.bridge.watching(),
        )
        if current is not None:
            view = replace(view, stages=self._stages(current, visible, card_in))
        self.panel.show_view(view)

        if view.state == "done" and current is not None:
            if not self._done_timer.isActive():
                self._done_timer.start(self.done_fade_ms)
        else:
            self._done_timer.stop()

        self._sync_rows([b for b in visible if current is None or b.id != current.id])
        self.mode.set_paced(self.bridge.settings.paced)
        self.speed.set_level(self.bridge.settings.speed)
        self._want_photo(current)
        self.pause.setText("Resume" if paused else "Pause")
        self.pause.setToolTip(PAUSE_TIPS[paused])
        self.pause.set_icon("play" if paused else "pause")
        self.activity.setVisible(bool(self.drawer.entries))

        if any(b.state in ("attention", "error") for b in visible):
            status = "attention"
        elif view.state == "safe" or (view.state == "ready" and view.card):
            status = "safe"
        elif self.bridge.has_pending():
            status = "working"
        else:
            status = "idle"
        self._offer_update(status)
        self._notice_gap.setVisible(self.notice_bar.isVisibleTo(self))
        if status != self._status:
            self._status = status
            self.status_changed.emit(status)

    def status(self) -> str:
        return self._status

    def _want_photo(self, current: Batch | None) -> None:
        """Show the newest photo of the active event behind the headline. It is looked
        for again when the event changes and as each card's photos land."""
        active = self.bridge.active()
        if active is None:
            self._photo_key = ()
            self.panel.set_photo(None)
            return
        key: tuple[object, ...] = (active.id,)
        if current is not None and current.event_id == active.id:
            key += (current.id, current.state)
        if key == self._photo_key:
            return
        self._photo_key = key

        def show(image: object) -> None:
            if self._photo_key == key:
                self.panel.set_photo(image if isinstance(image, QImage) else None)

        self.bridge.latest_photo(active.id, PHOTO_EDGE, show)

    def _stages(
        self, current: Batch, visible: list[Batch], card_in: bool
    ) -> tuple[StageView, StageView, StageView]:
        """Every stage follows the newest card. A stage the newest card has not reached
        yet shows the earlier card that is in it now (a new card copies while the one
        before it uploads); each stage names its card."""
        ejected = current.id in self._ejected
        views = list(stages_for(current, card_in=card_in, ejected=ejected))
        for index, states in ((1, ("safe", "processing", "ready")), (2, ("uploading", "held"))):
            if views[index].state != "pending":
                continue
            other = next((b for b in visible[1:] if b.state in states), None)
            if other is not None:
                views[index] = stages_for(other)[index]
        return views[0], views[1], views[2]

    def _offer_update(self, status: str) -> None:
        """Offer a newer version only while idle, so it never competes with a card."""
        update = self.bridge.update
        answered = (self.bridge.settings.skipped_update, self._update_opened)
        if self.bridge.installing_update:
            self.notice_bar.withdraw_update()
        elif update is not None and status == "idle" and update.version not in answered:
            self.notice_bar.offer_update(
                update.version, "Update now" if self.bridge.can_self_update else "Download"
            )
        else:
            self.notice_bar.withdraw_update()

    def _download_update(self) -> None:
        if self.bridge.update is not None:
            self._update_opened = self.bridge.update.version
        self.bridge.get_update()
        self.refresh()

    def _on_update_install(self, state: str, percent: int) -> None:
        version = self.bridge.update.version if self.bridge.update else ""
        if state == "downloading":
            self.notice_bar.show_progress(f"Downloading WeFlow {version} · {percent}%")
        elif state == "waiting":
            self.notice_bar.show_progress(
                f"WeFlow {version} is ready. It installs when these photos are done."
            )
        elif state == "installing":
            self.notice_bar.show_progress(f"Installing WeFlow {version}. It will reopen by itself.")
        else:
            self.notice_bar.show_progress("")
            self._update_opened = ""  # offer it again
            self.notice_bar.show_message(
                "error", "The update could not be downloaded. Try again later."
            )
        self.refresh()

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
        self._maybe_auto_eject(batch, previous)
        finished = batch.state == "done" and previous is not None and previous.state != "done"
        if finished and not batch.live:  # FR-48: no pop-up for every shot the camera sends
            self.notify.emit("done", "All done", f"{batch.label}: {self._done_text(batch)}")
        self.refresh()

    def _maybe_auto_eject(self, batch: Batch, previous: Batch | None) -> None:
        """Eject the card once every file on it is copied and verified, if asked to."""
        if not (self.bridge.settings.auto_eject and batch.is_card and batch.verified):
            return
        if batch.state == "error" or batch.id in self._auto_ejecting:
            return
        if previous is not None and previous.verified:
            return  # it was verified before this update: the moment has passed
        if batch.source not in self._cards:
            return
        self._auto_ejecting.add(batch.id)
        self._start_eject(batch)

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
        batch_id = self._ejecting.pop(path, None)
        auto = batch_id in self._auto_ejecting
        if ok:
            self._cards.pop(path, None)
            if batch_id is not None:
                self._ejected.add(batch_id)
            self.notice_bar.show_message("safe", "Card ejected. You can take it out.")
            self.drawer.add("info", "Card ejected.")
        else:
            if auto:  # the copy is safe either way; say so first
                message = "Safe to remove, but the card would not eject. Eject it yourself."
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
            self._start_eject(self._shown_batch)

    def _start_eject(self, batch: Batch) -> None:
        if batch.source in self._ejecting or batch.source in self._erasing.values():
            return
        self._ejecting[batch.source] = batch.id
        self.panel.set_ejecting(True)
        self.bridge.eject(batch.source)

    def _erase(self) -> None:
        """Erase the shown card's copied photos (FR-47): asked twice by the panel."""
        batch = self._shown_batch
        if batch is None or not (batch.is_card and batch.verified):
            return
        if batch.source in self._ejecting or batch.id in self._erasing:
            return
        self._erasing[batch.id] = batch.source
        self.panel.set_erasing(True)
        self.bridge.erase_card(batch.id)

    def _on_erase_finished(self, batch_id: int, ok: bool, message: str) -> None:
        self._erasing.pop(batch_id, None)
        self.panel.set_erasing(False)
        self.notice_bar.show_message("safe" if ok else "error", message)
        self.drawer.add("info" if ok else "error", message)
        self.refresh()

    def _release(self) -> None:
        if self._shown_batch is not None:
            self.bridge.release(self._shown_batch.event_id)

    def _process(self) -> None:
        if self._shown_batch is not None:
            self.bridge.process(self._shown_batch.event_id)

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
        elif name == "process":
            self.bridge.process(batch.event_id)
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
