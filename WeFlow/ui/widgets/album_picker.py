"""The gallery / album field: a "Choose Gallery…" button that opens a chooser window
listing the account's galleries, with "Create a gallery…" at the top (FR-39, FR-41)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..bridge import AlbumChoice, Bridge, UploadError, service_name
from ..theme import theme
from . import busy as busy_cover
from .common import Button, label

SERVICES = ("smugmug", "flickr")
NO_ALBUMS = {
    "smugmug": "No galleries yet. Choose Create a gallery.",
    "flickr": "No albums yet. Choose Create an album.",
}
FLICKR_LATER = "Flickr makes the album when the first photo goes up."
CREATE = "create"  # item data of the "Create a gallery…" row
LINK = "link"  # item data of the "Use this link" row
ROLE = Qt.ItemDataRole.UserRole


def album_word(destination: str, plural: bool = True) -> str:
    if destination == "smugmug":
        return "galleries" if plural else "gallery"
    return "albums" if plural else "album"


def labelled(choices: list[AlbumChoice]) -> dict[str, AlbumChoice]:
    """Shown text -> gallery, newest first; same-named galleries get their folder."""
    counts = Counter(choice.name for choice in choices)
    named: dict[str, AlbumChoice] = {}
    for choice in choices:
        text = choice.name
        if counts[choice.name] > 1 and choice.where:
            text = f"{choice.name} — {choice.where}"
        named.setdefault(text, choice)
    return named


class GalleryChooser(QDialog):
    """Pick one of the user's galleries, paste a link, or create a new one.

    Creating is its own step (the list is swapped out), so it is always clear
    which gallery the photos will go to. ``chosen`` is set when the window is
    accepted: an ``AlbumChoice``, or the pasted link as text.
    """

    def __init__(
        self,
        bridge: Bridge,
        destination: str,
        current: str = "",
        suggest: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.destination = destination
        self.chosen: AlbumChoice | str | None = None
        self.choices: list[AlbumChoice] = []
        self._current = current
        self._suggest = suggest
        self._token = 0
        self._new_token = 0
        word = album_word(destination, plural=False)
        article = "a" if destination == "smugmug" else "an"
        self.setWindowTitle(f"Choose {article} {word}")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(440, 520)

        # ------------------------------------------------------------ list page
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Search, or paste a gallery link" if destination == "smugmug" else "Search"
        )
        self.search.setAccessibleName("Search")
        self.search.textChanged.connect(self._filter)
        self.search.installEventFilter(self)  # arrow keys move through the list
        self.reload = Button(
            "",
            "quiet",
            icon="refresh-cw",
            icon_token="text-2",
            tip=f"Reload {album_word(destination)}",
        )
        self.reload.clicked.connect(lambda: self.load(refresh=True))
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(4)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.reload)

        self.list = QListWidget()
        self.list.setObjectName("GalleryList")
        self.list.setUniformItemSizes(True)
        self.list.setAccessibleName(album_word(destination).capitalize())
        self.list.currentItemChanged.connect(lambda *_: self._sync())
        self.list.itemActivated.connect(lambda _item: self.choose())
        self.create_row = QListWidgetItem(f"Create {article} {word}…")
        self.create_row.setData(ROLE, CREATE)
        self.link_row = QListWidgetItem("Use this link")
        self.link_row.setData(ROLE, LINK)
        self.hint = label("", "secondary", wrap=True)
        self.hint.hide()

        list_page = QWidget()
        list_column = QVBoxLayout(list_page)
        list_column.setContentsMargins(0, 0, 0, 0)
        list_column.setSpacing(8)
        list_column.addLayout(search_row)
        list_column.addWidget(self.list, 1)
        list_column.addWidget(self.hint)

        # ---------------------------------------------------------- create page
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText(f"New {word} name")
        self.new_name.setAccessibleName(self.new_name.placeholderText())
        self.new_name.textChanged.connect(self._sync)
        self.new_name.returnPressed.connect(self.create_album)
        self.new_hint = label(
            FLICKR_LATER if destination == "flickr" else "Photos will go to this new gallery.",
            "secondary",
            wrap=True,
        )
        self.new_error = label("", "error", wrap=True)
        self.new_error.hide()
        create_page = QWidget()
        create_column = QVBoxLayout(create_page)
        create_column.setContentsMargins(0, 0, 0, 0)
        create_column.setSpacing(8)
        create_column.addWidget(label(f"Name the new {word}", "label"))
        create_column.addWidget(self.new_name)
        create_column.addWidget(self.new_hint)
        create_column.addWidget(self.new_error)
        create_column.addStretch(1)

        self.pages = QStackedWidget()
        self.pages.addWidget(list_page)
        self.pages.addWidget(create_page)

        # --------------------------------------------------------------- footer
        self.back = Button("Back", "text")
        self.back.clicked.connect(self.show_list)
        self.cancel = Button("Cancel", "text")
        self.cancel.clicked.connect(self.reject)
        self.ok = Button("Choose", "primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.choose)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self.back)
        footer.addStretch(1)
        footer.addWidget(self.cancel)
        footer.addWidget(self.ok)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 16)
        outer.setSpacing(12)
        self.heading = label(self.windowTitle(), "title")
        outer.addWidget(self.heading)
        outer.addWidget(self.pages, 1)
        outer.addLayout(footer)

        theme.changed.connect(self._retheme)
        self._fill([])
        self._retheme()
        self.show_list()
        self.load()

    def _retheme(self) -> None:
        self.create_row.setIcon(theme.icon("plus", "accent", 16))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()  # type: ignore[attr-defined]
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._step(1 if key == Qt.Key.Key_Down else -1)
                return True
        return super().eventFilter(watched, event)

    def _step(self, direction: int) -> None:
        rows = [self.list.item(i) for i in range(self.list.count())]
        shown = [item for item in rows if not item.isHidden()]
        if not shown:
            return
        current = self.list.currentItem()
        index = shown.index(current) + direction if current in shown else 0
        self.list.setCurrentItem(shown[max(0, min(len(shown) - 1, index))])

    # ----------------------------------------------------------------- list
    def load(self, refresh: bool = False) -> None:
        """The galleries are usually ready already (fetched at sign-in); Reload
        asks the service again."""
        self._token += 1
        token = self._token
        self.reload.setEnabled(False)
        self._show_hint(f"Loading your {album_word(self.destination)}…")
        if refresh:
            busy_cover.begin(self)

        def answered(result: object) -> None:
            if refresh:
                busy_cover.end(self)
            if token == self._token:
                self._on_list(result)

        self.bridge.list_albums(self.destination, answered, refresh=refresh)

    def _on_list(self, result: object) -> None:
        self.reload.setEnabled(True)
        if not isinstance(result, list):
            message = result.message if isinstance(result, UploadError) else ""
            self._show_hint(
                message
                or f"Can't load your {service_name(self.destination)} "
                f"{album_word(self.destination)}. Press Reload to try again."
            )
            return
        self._fill(result)
        self._show_hint("" if result else NO_ALBUMS.get(self.destination, ""))

    def _fill(self, choices: list[AlbumChoice]) -> None:
        self.choices = choices
        named = labelled(choices)
        picked = self.list.currentItem()
        keep = picked.text() if picked is not None and picked.data(ROLE) is None else ""
        self.list.blockSignals(True)
        while self.list.count():
            self.list.takeItem(0)  # keeps the create and link rows alive
        self.list.addItem(self.create_row)
        self.list.addItem(self.link_row)
        selected: QListWidgetItem | None = None
        for text in named:
            item = QListWidgetItem(text)
            self.list.addItem(item)
            if text == (keep or self._current):
                selected = item
        self.named = named
        self.list.blockSignals(False)
        self._filter()
        if selected is not None:
            self.list.setCurrentItem(selected)
            self.list.scrollToItem(selected)
        self._sync()

    def _filter(self) -> None:
        text = self.search.text().strip()
        is_link = self.destination == "smugmug" and text.lower().startswith("http")
        self.link_row.setHidden(not is_link)
        needle = "" if is_link else text.casefold()
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(ROLE) is None:
                item.setHidden(bool(needle) and needle not in item.text().casefold())
        current = self.list.currentItem()
        if is_link:
            self.list.setCurrentItem(self.link_row)
        elif current is None or current.isHidden():
            first = next(
                (
                    self.list.item(i)
                    for i in range(self.list.count())
                    if not self.list.item(i).isHidden() and self.list.item(i).data(ROLE) is None
                ),
                None,
            )
            if first is not None and needle:
                self.list.setCurrentItem(first)
        self._sync()

    def _show_hint(self, text: str) -> None:
        self.hint.setText(text)
        self.hint.setVisible(bool(text))

    def _selected(self) -> QListWidgetItem | None:
        item = self.list.currentItem()
        return None if item is None or item.isHidden() else item

    def _sync(self) -> None:
        if self.pages.currentIndex() == 1:
            self.ok.setText("Create")
            self.ok.setEnabled(bool(self.new_name.text().strip()) and self.new_name.isEnabled())
            return
        item = self._selected()
        self.ok.setText("Next" if item is not None and item.data(ROLE) == CREATE else "Choose")
        self.ok.setEnabled(item is not None)

    def select(self, text: str) -> None:
        """Highlight the gallery shown as ``text`` (tests and keyboard helpers)."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(ROLE) is None and item.text() == text:
                self.list.setCurrentItem(item)
                return

    def choose(self) -> None:
        if self.pages.currentIndex() == 1:
            self.create_album()
            return
        item = self._selected()
        if item is None:
            return
        kind = item.data(ROLE)
        if kind == CREATE:
            self.show_create()
        elif kind == LINK:
            self.chosen = self.search.text().strip()
            self.accept()
        else:
            self.chosen = self.named[item.text()]
            self.accept()

    # ------------------------------------------------------------------ new
    def show_create(self) -> None:
        name = self.search.text().strip()
        if not name or name.lower().startswith("http"):
            name = self._suggest
        self.new_name.setText(name)
        self.new_error.hide()
        self.pages.setCurrentIndex(1)
        self.heading.setText(f"New {album_word(self.destination, plural=False)}")
        self.back.show()
        self._sync()
        self.new_name.setFocus()
        self.new_name.selectAll()

    def show_list(self) -> None:
        self._new_token += 1  # ignore a late answer
        self.new_name.setEnabled(True)
        self.pages.setCurrentIndex(0)
        self.heading.setText(self.windowTitle())
        self.back.hide()
        self._sync()
        self.search.setFocus()

    def create_album(self) -> None:
        """FR-41: make the gallery off the UI thread, then choose it."""
        name = self.new_name.text().strip()
        if not name or not self.new_name.isEnabled():
            return
        self._new_token += 1
        token = self._new_token
        self.new_name.setEnabled(False)
        self.new_error.hide()
        self._sync()
        busy_cover.begin(self)

        def done(result: object) -> None:
            busy_cover.end(self)
            if token != self._new_token:
                return  # Back was pressed meanwhile
            self.new_name.setEnabled(True)
            self._sync()
            if not isinstance(result, AlbumChoice):
                message = result.message if isinstance(result, UploadError) else ""
                word = album_word(self.destination, plural=False)
                self.new_error.setText(message or f"Can't make the {word}. Try again.")
                self.new_error.show()
                return
            self.choices = [result, *[c for c in self.choices if c != result]]
            self.chosen = result
            self.accept()

        self.bridge.create_album(self.destination, name, done)

    def done(self, result: int) -> None:  # noqa: D102 - Qt override
        self._token += 1
        self._new_token += 1
        super().done(result)


class AlbumPicker(QWidget):
    """Captioned field showing the event's gallery, with a "Choose Gallery…" button.

    ``album`` is the event's album text: a listed name maps to that gallery, a
    pasted link is used as is.
    """

    changed = Signal()
    chooser_opened = Signal(object)  # the GalleryChooser, for tests

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
        self.albums: dict[str, AlbumChoice] = {}  # shown text -> gallery
        self.albums_for = ""  # destination the list belongs to
        self._token = 0
        self.chooser: GalleryChooser | None = None

        self.caption = label("Album", "label")
        self.album = QLineEdit(album_name or album)
        self.album.setReadOnly(True)
        self.album.setCursor(Qt.CursorShape.PointingHandCursor)
        self.album.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.album.textChanged.connect(lambda _text: self.changed.emit())
        self.album.installEventFilter(self)  # a click in the box opens the chooser
        self.choose = Button("Choose Gallery…")
        self.choose.clicked.connect(self.open_chooser)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.album, 1)
        row.addWidget(self.choose)

        self.error = label("", "error", wrap=True)
        self.error.hide()
        self.hint = label("", "secondary", wrap=True)
        self.hint.hide()

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(self.caption)
        column.addLayout(row)
        column.addWidget(self.error)
        column.addWidget(self.hint)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._relabel()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.album and event.type() == QEvent.Type.MouseButtonRelease:
            self.open_chooser()
        return super().eventFilter(watched, event)

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
        self.albums, self.albums_for = {}, ""
        self.album.blockSignals(True)
        self.album.clear()
        self.album.blockSignals(False)
        self._show_hint("")
        self._relabel()
        self.changed.emit()
        self.load()

    def set_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))

    def _relabel(self) -> None:
        smugmug = self._destination == "smugmug"
        self.caption.setText("Gallery" if smugmug else "Album")
        self.album.setAccessibleName(self.caption.text())
        self.album.setPlaceholderText(f"No {album_word(self._destination, plural=False)} chosen")
        self.choose.setText("Choose Gallery…" if smugmug else "Choose Album…")

    def _show_hint(self, text: str) -> None:
        self.hint.setText(text)
        self.hint.setVisible(bool(text))

    def _set_choices(self, choices: list[AlbumChoice]) -> None:
        self.albums, self.albums_for = labelled(choices), self._destination

    # ----------------------------------------------------------------- list
    def load(self, refresh: bool = False) -> None:
        """Quietly get the gallery list (usually already fetched at sign-in) so the
        event's gallery shows by name. Nothing is shown while it loads."""
        destination = self._destination
        self._token += 1
        token = self._token
        if destination not in SERVICES:
            return

        def answered(result: object) -> None:
            if token != self._token or not isinstance(result, list):
                return
            match = self.bridge.match_album(result, self.fields()["album"])
            self._set_choices(result)
            if match is not None:
                self._show_choice(match)

        self.bridge.list_albums(destination, answered, refresh=refresh)

    def _show_choice(self, choice: AlbumChoice) -> None:
        text = next(t for t, c in self.albums.items() if c == choice)
        if self.album.text() != text:
            self.album.setText(text)
        else:
            self.changed.emit()

    # --------------------------------------------------------------- chooser
    def open_chooser(self) -> None:
        if self._destination not in SERVICES or self.chooser is not None:
            return
        chooser = GalleryChooser(
            self.bridge,
            self._destination,
            self.album.text().strip(),
            self._suggest(),
            self.window(),
        )
        chooser.finished.connect(lambda _result: self._on_chooser(chooser))
        self.chooser = chooser
        chooser.open()
        self.chooser_opened.emit(chooser)

    def _on_chooser(self, chooser: GalleryChooser) -> None:
        self.chooser = None
        chooser.deleteLater()
        chosen = chooser.chosen
        if chooser.destination != self._destination or chosen is None:
            return
        if isinstance(chosen, AlbumChoice):
            self._set_choices(chooser.choices)
            self._show_choice(chosen)
            self._show_hint(FLICKR_LATER if self._destination == "flickr" else "")
        else:
            self._show_hint("")
            self.album.setText(chosen)  # a pasted link
