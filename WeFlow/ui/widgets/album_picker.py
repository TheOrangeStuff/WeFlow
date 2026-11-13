"""The gallery / album box: pick from the account, reload, or make a new one (FR-39, FR-41)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..bridge import AlbumChoice, Bridge, UploadError, service_name
from .common import Button, label

PLACEHOLDERS = {
    "smugmug": "Pick or paste a link",
    "flickr": "Pick or type a title",
}
NO_ALBUMS = {
    "smugmug": "No galleries yet. Press + to make one.",
    "flickr": "No albums yet. Press + to make one.",
}
FLICKR_LATER = "Flickr makes the album when the first photo goes up."


def album_word(destination: str, plural: bool = True) -> str:
    word = "gallery" if destination == "smugmug" else "album"
    if plural:
        return "galleries" if destination == "smugmug" else "albums"
    return word


class AlbumPicker(QWidget):
    """Captioned, searchable dropdown of the user's galleries, with Reload and New.

    The text is the event's album field: a listed name maps to that gallery,
    anything else (a pasted link, a typed Flickr title) is used as typed.
    """

    changed = Signal()

    def __init__(
        self,
        bridge: Bridge,
        destination: str,
        album: str = "",
        album_name: str = "",
        suggest: Callable[[], str] = lambda: "",
    ) -> None:
        super().__init__()
        self.bridge = bridge
        self._saved = (destination, album, album_name)
        self._destination = destination
        self._suggest = suggest
        self._choices: list[AlbumChoice] = []
        self.albums: dict[str, AlbumChoice] = {}  # shown text -> gallery
        self.albums_for = ""  # destination the list belongs to
        self._token = 0
        self._new_token = 0  # bumped on Create and Cancel; stale answers are dropped

        self.caption = label("Album", "label")
        self.gallery = QComboBox()
        self.gallery.setEditable(True)
        self.gallery.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.gallery.setMaxVisibleItems(12)
        self.gallery.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        finder = QCompleter(self.gallery.model(), self.gallery)
        finder.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        finder.setFilterMode(Qt.MatchFlag.MatchContains)
        finder.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        finder.popup().setObjectName("AlbumFinder")
        self.gallery.setCompleter(finder)
        line = self.gallery.lineEdit()
        assert line is not None
        self.album: QLineEdit = line
        self.album.setText(album_name or album)
        self.album.textChanged.connect(self.changed)
        self.reload = Button("", "quiet", icon="refresh-cw", icon_token="text-2")
        self.reload.clicked.connect(self.load)
        self.new = Button("", "quiet", icon="plus", icon_token="text-2")
        self.new.clicked.connect(self.open_new)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.gallery, 1)
        row.addWidget(self.reload)
        row.addWidget(self.new)

        self.error = label("", "error", wrap=True)
        self.error.hide()
        self.hint = label("", "secondary", wrap=True)
        self.hint.hide()

        # The "new gallery" row, shown when + is pressed.
        self.new_name = QLineEdit()
        self.new_name.returnPressed.connect(self.create_album)
        self.new_name.textChanged.connect(self._sync_create)
        self.create_button = Button("Create")
        self.create_button.clicked.connect(self.create_album)
        self.cancel_button = Button("Cancel", "text")
        self.cancel_button.clicked.connect(self.close_new)
        self.new_row = QWidget()
        new_line = QHBoxLayout(self.new_row)
        new_line.setContentsMargins(0, 0, 0, 0)
        new_line.setSpacing(4)
        new_line.addWidget(self.new_name, 1)
        new_line.addWidget(self.create_button)
        new_line.addWidget(self.cancel_button)
        self.new_row.hide()

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(self.caption)
        column.addLayout(row)
        column.addWidget(self.error)
        column.addWidget(self.hint)
        column.addWidget(self.new_row)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._relabel()

    # ---------------------------------------------------------------- state
    def fields(self) -> dict[str, str]:
        """``album`` and ``album_name`` for what the box now shows (FR-39)."""
        text = self.album.text().strip()
        choice = self.albums.get(text) if self.albums_for == self._destination else None
        if choice is not None:
            return {"album": choice.value, "album_name": choice.name}
        destination, album, album_name = self._saved
        if text and album_name and destination == self._destination and text == album_name:
            return {"album": album, "album_name": album_name}  # list not loaded (yet)
        return {"album": text, "album_name": ""}

    def set_destination(self, destination: str) -> None:
        """A different service: its own list, and no gallery from the old one."""
        if destination == self._destination:
            return
        self._destination = destination
        self.close_new()
        self.gallery.blockSignals(True)
        self.gallery.clear()
        self.gallery.blockSignals(False)
        self._choices, self.albums, self.albums_for = [], {}, ""
        self._show_hint("")
        self._relabel()
        self.load()

    def set_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))

    def _relabel(self) -> None:
        destination = self._destination
        self.caption.setText("Gallery" if destination == "smugmug" else "Album")
        self.gallery.setAccessibleName(self.caption.text())
        self.album.setPlaceholderText(PLACEHOLDERS.get(destination, ""))
        for button, verb in ((self.reload, "Reload"), (self.new, "New")):
            text = f"{verb} {album_word(destination, plural=verb == 'Reload')}"
            button.setToolTip(text)
            button.setAccessibleName(text)
        self.new_name.setPlaceholderText(f"New {album_word(destination, plural=False)} name")
        self.new_name.setAccessibleName(self.new_name.placeholderText())

    def _show_hint(self, text: str) -> None:
        self.hint.setText(text)
        self.hint.setVisible(bool(text))

    # ----------------------------------------------------------------- list
    def load(self) -> None:
        """Ask the service for the user's galleries, off the UI thread."""
        destination = self._destination
        self._token += 1
        token = self._token
        if destination not in PLACEHOLDERS:
            return
        self._show_hint(f"Loading your {album_word(destination)}…")
        self.reload.setEnabled(False)
        self.bridge.list_albums(destination, lambda result: self._on_list(token, result))

    def _on_list(self, token: int, result: object) -> None:
        if token != self._token:
            return  # the destination changed, or Reload was pressed again
        self.reload.setEnabled(True)
        destination = self._destination
        if not isinstance(result, list):
            if isinstance(result, UploadError):
                self._show_hint(result.message)
            else:
                self._show_hint(
                    f"Can't load your {service_name(destination)} {album_word(destination)}. "
                    "Press Reload to try again."
                )
            return
        self._show(result, self.bridge.match_album(result, self.fields()["album"]))
        self._show_hint("" if result else NO_ALBUMS[destination])

    def _show(self, choices: list[AlbumChoice], picked: AlbumChoice | None) -> None:
        """Fill the dropdown, newest first, and show ``picked`` (or keep the text)."""
        names = [choice.name for choice in choices]
        shown: dict[str, AlbumChoice] = {}
        for choice in choices:
            text = choice.name
            if names.count(choice.name) > 1 and choice.where:
                text = f"{choice.name} — {choice.where}"
            shown.setdefault(text, choice)
        current = self.fields()
        self.gallery.blockSignals(True)
        self.album.blockSignals(True)
        self.gallery.clear()
        self.gallery.addItems(list(shown))
        self._choices, self.albums, self.albums_for = choices, shown, self._destination
        text = current["album_name"] or current["album"]
        if picked is not None:
            text = next(t for t, c in shown.items() if c == picked)
        self.album.setText(text)
        self.gallery.blockSignals(False)
        self.album.blockSignals(False)
        self.changed.emit()

    # ------------------------------------------------------------------ new
    def open_new(self) -> None:
        self.new_name.setText(self._suggest())
        self.new_row.show()
        self._sync_create()
        self.new_name.setFocus()
        self.new_name.selectAll()

    def close_new(self) -> None:
        self.new_row.hide()
        self._new_token += 1  # ignore a late answer

    def _sync_create(self) -> None:
        self.create_button.setEnabled(bool(self.new_name.text().strip()))

    def create_album(self) -> None:
        """FR-41: make the gallery off the UI thread, then pick it."""
        name = self.new_name.text().strip()
        if not name or not self.create_button.isEnabled():
            return
        destination = self._destination
        self._new_token += 1
        token = self._new_token
        self.create_button.setEnabled(False)
        self.new_name.setEnabled(False)
        self._show_hint(f"Making the {album_word(destination, plural=False)}…")
        self.bridge.create_album(
            destination, name, lambda result: self._on_created(token, destination, result)
        )

    def _on_created(self, token: int, destination: str, result: object) -> None:
        self.new_name.setEnabled(True)
        self._sync_create()
        if token != self._new_token or destination != self._destination:
            return  # cancelled, or the service changed meanwhile
        if not isinstance(result, AlbumChoice):
            message = result.message if isinstance(result, UploadError) else ""
            self._show_hint(
                message or f"Can't make the {album_word(destination, plural=False)}. Try again."
            )
            return
        self.new_row.hide()
        self._show([result, *[c for c in self._choices if c != result]], result)
        self._show_hint(FLICKR_LATER if destination == "flickr" else "")
