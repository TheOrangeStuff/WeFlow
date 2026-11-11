"""First run: library folder → connect a service (skippable) → first event (§7.4)."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .bridge import SERVICES, Bridge, Profile
from .event_editor import DESTINATION_LABELS, error_label, field
from .settings_dialog import LibraryRow, ServiceRow
from .widgets.common import Button, label, restyle

STEPS = ("Where should photos be saved?", "Connect a service", "Create your first event")
INTROS = (
    "WeFlow keeps one folder per event here, with originals and finished photos.",
    "Connect SmugMug or Flickr to upload automatically. You can also do this later in Settings.",
    "An event holds every choice, so inserting a card asks nothing. You can change it later.",
)


class FirstRun(QDialog):
    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.created: Profile | None = None
        self.setWindowTitle("Welcome to WeFlow")
        self.setFixedSize(520, 440)
        if not bridge.settings.library:
            bridge.update_settings(library=bridge.library())

        self.step_label = label("", "label")
        self.heading = label("", "title")
        self.intro = label("", "secondary", wrap=True)

        self.library = LibraryRow(bridge)
        self.services = {service: ServiceRow(bridge, service) for service in SERVICES}
        services = QWidget()
        services_column = QVBoxLayout(services)
        services_column.setContentsMargins(0, 0, 0, 0)
        services_column.setSpacing(12)
        for row in self.services.values():
            services_column.addWidget(row)
        services_column.addStretch(1)

        draft = bridge.draft_event()
        self._draft = draft
        self.name = QLineEdit(draft.name)
        self.name_error = error_label()
        self.destination = QComboBox()
        for value, text in DESTINATION_LABELS:
            self.destination.addItem(text, value)
        self.album = QLineEdit()
        self.album_error = error_label()
        self.album_field = field("Album", self.album, self.album_error)
        event_page = QWidget()
        event_column = QVBoxLayout(event_page)
        event_column.setContentsMargins(0, 0, 0, 0)
        event_column.setSpacing(12)
        event_column.addWidget(field("Event name", self.name, self.name_error))
        event_column.addWidget(field("Where photos go", self.destination))
        event_column.addWidget(self.album_field)
        event_column.addStretch(1)
        for signal in (self.name.textChanged, self.album.textChanged):
            signal.connect(self._validate)
        self.destination.currentIndexChanged.connect(self._validate)

        library_page = QWidget()
        library_column = QVBoxLayout(library_page)
        library_column.setContentsMargins(0, 0, 0, 0)
        library_column.addWidget(self.library)
        library_column.addStretch(1)

        self.pages = QStackedWidget()
        for page in (library_page, services, event_page):
            self.pages.addWidget(page)

        self.back = Button("Back")
        self.next = Button("Next", "primary")
        self.next.setDefault(True)
        self.back.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.next.clicked.connect(self._advance)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self.back)
        footer.addStretch(1)
        footer.addWidget(self.next)

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 24, 24, 16)
        column.setSpacing(8)
        column.addWidget(self.step_label)
        column.addWidget(self.heading)
        column.addWidget(self.intro)
        column.addSpacing(16)
        column.addWidget(self.pages, 1)
        column.addLayout(footer)
        bridge.services_changed.connect(self._update_buttons)
        bridge.refresh_services()
        self.go(0)

    def current_event(self) -> Profile:
        return replace(
            self._draft,
            name=self.name.text().strip(),
            destination=self.destination.currentData(),
            album=self.album.text().strip(),
        )

    def _validate(self) -> None:
        event = self.current_event()
        found = event.validate(self.bridge.other_names(event.id))
        self.album_field.setVisible(event.destination != "none")
        for key, line, text in (
            ("name", self.name, self.name_error),
            ("album", self.album, self.album_error),
        ):
            message = found.get(key, "")
            text.setText(message)
            text.setVisible(bool(message))
            restyle(line, invalid=bool(message))
        self._update_buttons()

    def _update_buttons(self) -> None:
        index = self.pages.currentIndex()
        self.back.setVisible(index > 0)
        if index == 1:
            connected = any(self.bridge.connected.values())
            self.next.setText("Next" if connected else "Skip for now")
            self.next.setEnabled(True)
        elif index == 2:
            self.next.setText("Start")
            event = self.current_event()
            self.next.setEnabled(not event.validate(self.bridge.other_names(event.id)))
        else:
            self.next.setText("Next")
            self.next.setEnabled(True)

    def go(self, index: int) -> None:
        index = max(0, min(2, index))
        self.pages.setCurrentIndex(index)
        self.step_label.setText(f"Step {index + 1} of 3")
        self.heading.setText(STEPS[index])
        self.intro.setText(INTROS[index])
        if index == 2:
            self._validate()
            self.name.setFocus()
            self.name.selectAll()
        self._update_buttons()

    def _advance(self) -> None:
        index = self.pages.currentIndex()
        if index < 2:
            self.go(index + 1)
            return
        event = self.current_event()
        if event.validate(self.bridge.other_names(event.id)):
            return
        self.created = self.bridge.save_event(event)
        self.bridge.finish_first_run()
        self.accept()
