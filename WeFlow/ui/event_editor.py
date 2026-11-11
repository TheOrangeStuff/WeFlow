"""Event setup sheet: destination, look, watermark, and a live preview (§7.4, FR-04)."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .bridge import FLICKR_PRIVACY, Bridge, Profile
from .theme import theme
from .widgets.common import Button, Toggle, label, restyle

PREVIEW_DEBOUNCE_MS = 150
DESTINATION_LABELS = [
    ("none", "This computer only"),
    ("smugmug", "SmugMug"),
    ("flickr", "Flickr"),
]
PRIVACY_LABELS = {
    "private": "Only me",
    "friends": "Friends",
    "family": "Family",
    "friends+family": "Friends and family",
    "public": "Anyone",
}
GRID = {(0, 0): "tl", (0, 2): "tr", (1, 1): "center", (2, 0): "bl", (2, 2): "br"}
POSITION_NAMES = {
    "tl": "Top left",
    "tr": "Top right",
    "center": "Center",
    "bl": "Bottom left",
    "br": "Bottom right",
}


def field(caption: str, control: QWidget, error: QLabel | None = None) -> QWidget:
    """A captioned control, with room for red helper text underneath."""
    box = QWidget()
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)
    column.addWidget(label(caption, "label"))
    column.addWidget(control)
    if error is not None:
        column.addWidget(error)
    control.setAccessibleName(caption)
    box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    return box


def error_label() -> QLabel:
    text = label("", "error", wrap=True)
    text.hide()
    return text


def toggle_row(caption: str, toggle: Toggle) -> QWidget:
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(label(caption, "body"), 1)
    row.addWidget(toggle)
    toggle.setAccessibleName(caption)
    return box


class SliderRow(QWidget):
    """A captioned slider that shows its value, e.g. "Size  18%"."""

    changed = Signal()

    def __init__(self, caption: str, low: int, high: int, value: int, suffix: str = "%") -> None:
        super().__init__()
        self._suffix = suffix
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(low, high)
        self.slider.setValue(value)
        self.slider.setAccessibleName(caption)
        self._value = label("", "label")
        head = QHBoxLayout()
        head.addWidget(label(caption, "label"), 1)
        head.addWidget(self._value)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addLayout(head)
        column.addWidget(self.slider)
        self.slider.valueChanged.connect(self._on_change)
        self._on_change()

    def _on_change(self) -> None:
        self._value.setText(f"{self.slider.value()}{self._suffix}")
        self.changed.emit()

    def value(self) -> int:
        return self.slider.value()


class PositionPicker(QWidget):
    """3×3 grid; only the corners and the center can be chosen."""

    changed = Signal()

    def __init__(self, position: str) -> None:
        super().__init__()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.cells: dict[str, Button] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for row in range(3):
            for col in range(3):
                cell = Button(kind="cell")
                cell.setCheckable(True)
                place = GRID.get((row, col))
                if place is None:
                    cell.setEnabled(False)
                else:
                    cell.setAccessibleName(POSITION_NAMES[place])
                    cell.setToolTip(POSITION_NAMES[place])
                    cell.setChecked(place == position)
                    self.cells[place] = cell
                    self.group.addButton(cell)
                grid.addWidget(cell, row, col)
        self.group.buttonToggled.connect(lambda _button, on: self.changed.emit() if on else None)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def position(self) -> str:
        return next((place for place, cell in self.cells.items() if cell.isChecked()), "br")


class PreviewSplit(QWidget):
    """Before on the left of the line, after on the right. Drag the line to compare."""

    def __init__(self) -> None:
        super().__init__()
        self._before: QPixmap | None = None
        self._after: QPixmap | None = None
        self.split = 0.5
        self.message = "Choose a sample photo to preview the look."
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setAccessibleName("Before and after preview")
        theme.changed.connect(self.update)

    def has_images(self) -> bool:
        return self._before is not None and self._after is not None

    def set_images(self, before: QImage, after: QImage) -> None:
        self._before, self._after = QPixmap.fromImage(before), QPixmap.fromImage(after)
        self.update()

    def set_message(self, message: str) -> None:
        self._before = self._after = None
        self.message = message
        self.update()

    def _image_rect(self) -> QRectF:
        if self._after is None:
            return QRectF(self.rect())
        size = self._after.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF(
            (self.width() - size.width()) / 2,
            (self.height() - size.height()) / 2,
            size.width(),
            size.height(),
        )

    def _set_split_from(self, x: float) -> None:
        rect = self._image_rect()
        if rect.width() > 0:
            self.split = min(1.0, max(0.0, (x - rect.left()) / rect.width()))
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._set_split_from(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_split_from(event.position().x())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Left:
            self.split = max(0.0, self.split - 0.05)
        elif event.key() == Qt.Key.Key_Right:
            self.split = min(1.0, self.split + 0.05)
        else:
            super().keyPressEvent(event)
            return
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("surface-2"))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)
        if self._before is None or self._after is None:
            painter.setPen(theme.color("text-2"))
            painter.drawText(
                QRectF(self.rect()).adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.message,
            )
            return
        rect = self._image_rect()
        x = rect.left() + rect.width() * self.split
        painter.drawPixmap(rect, self._after, QRectF(self._after.rect()))
        painter.save()
        painter.setClipRect(QRectF(rect.left(), rect.top(), x - rect.left(), rect.height()))
        painter.drawPixmap(rect, self._before, QRectF(self._before.rect()))
        painter.restore()
        painter.setPen(QPen(theme.color("surface"), 2))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setPen(QPen(theme.color("text-2"), 1))
        painter.setBrush(theme.color("surface"))
        painter.drawEllipse(QPointF(x, rect.center().y()), 8, 8)
        if self.hasFocus():
            painter.setPen(QPen(theme.color("accent"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)


class EventEditor(QDialog):
    """Create or edit one event. Saving a new event also makes it the active one."""

    def __init__(self, bridge: Bridge, event: Profile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._event = event
        self.is_new = not any(e.id == event.id for e in bridge.events())
        self._other_names = bridge.other_names(event.id)
        self._token = 0
        self._watermark_broken = ""
        self._watermark_path = event.watermark_path
        self.saved: Profile | None = None
        self.previews_rendered = 0
        self.setWindowTitle("New event" if self.is_new else "Edit event")
        self.setWindowModality(Qt.WindowModality.WindowModal)  # a sheet on macOS
        self.setFixedWidth(640)
        self.resize(640, 680)

        # ------------------------------------------------------------- fields
        self.name = QLineEdit(event.name)
        self.name_error = error_label()
        self.destination = QComboBox()
        for value, text in DESTINATION_LABELS:
            self.destination.addItem(text, value)
        self.destination.setCurrentIndex(
            max(0, [v for v, _ in DESTINATION_LABELS].index(event.destination))
        )
        self.album = QLineEdit(event.album)
        self.album_error = error_label()
        self.album_field = field("Album", self.album, self.album_error)
        self._album_caption = self.album_field.findChild(QLabel)
        self.privacy = QComboBox()
        for value in FLICKR_PRIVACY:
            self.privacy.addItem(PRIVACY_LABELS[value], value)
        self.privacy.setCurrentIndex(FLICKR_PRIVACY.index(event.flickr_privacy))
        self.privacy_field = field("Who can see the photos", self.privacy)
        self.keywords = QLineEdit(event.keywords)
        self.keywords.setPlaceholderText("wedding, smith, 2026")
        self.keywords_field = field("Keywords", self.keywords)
        self.hold = Toggle(event.hold_uploads)
        self.hold_row = toggle_row("Hold uploads for review", self.hold)

        self.auto_wb = Toggle(event.auto_wb)
        self.wb_strength = SliderRow("Strength", 0, 100, round(event.wb_strength * 100))
        self.auto_tone = Toggle(event.auto_tone)

        self.watermark = Toggle(event.watermark)
        self.watermark_thumb = QLabel()
        self.watermark_thumb.setFixedSize(32, 32)
        self.watermark_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.watermark_thumb.setProperty("type", "thumb")
        self.watermark_name = label("", "secondary")
        self.watermark_choose = Button("Choose PNG…")
        self.watermark_choose.clicked.connect(self._choose_watermark)
        self.watermark_error = error_label()
        self.position = PositionPicker(event.watermark_position)
        self.size = SliderRow("Size", 5, 50, round(event.watermark_scale * 100))
        self.opacity = SliderRow("Opacity", 10, 100, round(event.watermark_opacity * 100))

        self.quality = QSpinBox()
        self.quality.setRange(60, 100)
        self.quality.setValue(event.jpeg_quality)
        self.resize_edge = QSpinBox()
        self.resize_edge.setRange(0, 20000)
        self.resize_edge.setSingleStep(100)
        self.resize_edge.setSpecialValueText("Full size")
        self.resize_edge.setSuffix(" px")
        self.resize_edge.setValue(event.max_long_edge)
        self.margin = SliderRow("Watermark margin", 0, 10, round(event.watermark_margin * 100))

        # --------------------------------------------------------------- left
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 8, 0)
        left.setSpacing(12)
        left.addWidget(field("Event name", self.name, self.name_error))
        left.addSpacing(4)
        left.addWidget(label("Destination", "heading"))
        left.addWidget(field("Where photos go", self.destination))
        left.addWidget(self.album_field)
        left.addWidget(self.privacy_field)
        left.addWidget(self.keywords_field)
        left.addWidget(self.hold_row)
        left.addSpacing(4)
        left.addWidget(label("Look", "heading"))
        left.addWidget(toggle_row("Auto white balance", self.auto_wb))
        left.addWidget(self.wb_strength)
        left.addWidget(toggle_row("Auto tone", self.auto_tone))
        left.addSpacing(4)
        left.addWidget(label("Watermark", "heading"))
        left.addWidget(toggle_row("Add a watermark", self.watermark))
        picker = QHBoxLayout()
        picker.setSpacing(8)
        picker.addWidget(self.watermark_thumb)
        picker.addWidget(self.watermark_name, 1)
        picker.addWidget(self.watermark_choose)
        place = QHBoxLayout()
        place.setSpacing(16)
        place.addWidget(self.position, 0, Qt.AlignmentFlag.AlignTop)
        sliders = QVBoxLayout()
        sliders.setSpacing(8)
        sliders.addWidget(self.size)
        sliders.addWidget(self.opacity)
        place.addLayout(sliders, 1)
        self.watermark_box = QWidget()
        wm = QVBoxLayout(self.watermark_box)
        wm.setContentsMargins(0, 0, 0, 0)
        wm.setSpacing(8)
        wm.addLayout(picker)
        wm.addWidget(self.watermark_error)
        wm.addLayout(place)
        left.addWidget(self.watermark_box)
        left.addSpacing(4)
        self.more = Button("More", "quiet", icon="chevron-right", icon_token="text-2")
        self.more.clicked.connect(self._toggle_more)
        left.addWidget(self.more)
        self.more_box = QWidget()
        more = QVBoxLayout(self.more_box)
        more.setContentsMargins(0, 0, 0, 0)
        more.setSpacing(12)
        more.addWidget(field("JPEG quality", self.quality))
        more.addWidget(field("Resize long edge", self.resize_edge))
        more.addWidget(self.margin)
        self.more_box.hide()
        left.addWidget(self.more_box)
        left.addStretch(1)
        form = QWidget()
        form.setLayout(left)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(form)
        scroll.setFixedWidth(304)

        # -------------------------------------------------------------- right
        self.preview = PreviewSplit()
        self.sample_button = Button("Choose sample photo…")
        self.sample_button.clicked.connect(self._choose_sample)
        captions = QHBoxLayout()
        captions.addWidget(label("Before", "label"))
        captions.addStretch(1)
        captions.addWidget(label("After", "label"))
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addLayout(captions)
        right.addWidget(self.preview, 1)
        right.addWidget(self.sample_button, 0, Qt.AlignmentFlag.AlignLeft)

        columns = QHBoxLayout()
        columns.setSpacing(24)
        columns.addWidget(scroll)
        columns.addLayout(right, 1)

        # ------------------------------------------------------------- footer
        self.delete = Button("Delete", "quiet")
        self.duplicate = Button("Duplicate", "quiet")
        self.footer_error = error_label()
        self.cancel = Button("Cancel")
        self.save = Button("Save", "primary")
        self.save.setDefault(True)
        self.delete.clicked.connect(self._delete)
        self.duplicate.clicked.connect(self._duplicate)
        self.cancel.clicked.connect(self.reject)
        self.save.clicked.connect(self._save)
        self.delete.setVisible(not self.is_new)
        self.duplicate.setVisible(not self.is_new)
        self._delete_armed = QTimer(self)
        self._delete_armed.setSingleShot(True)
        self._delete_armed.timeout.connect(lambda: self.delete.setText("Delete"))
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self.delete)
        footer.addWidget(self.duplicate)
        footer.addWidget(self.footer_error, 1)
        footer.addStretch(1)
        footer.addWidget(self.cancel)
        footer.addWidget(self.save)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 16)
        outer.setSpacing(16)
        outer.addWidget(label("New event" if self.is_new else "Edit event", "title"))
        outer.addLayout(columns, 1)
        outer.addLayout(footer)

        # ------------------------------------------------------------- wiring
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(PREVIEW_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._request_preview)
        for line in (self.name, self.album, self.keywords):
            line.textChanged.connect(self._changed)
        for combo in (self.destination, self.privacy):
            combo.currentIndexChanged.connect(self._changed)
        for toggle in (self.hold, self.auto_wb, self.auto_tone, self.watermark):
            toggle.toggled.connect(self._changed)
        for slider in (self.wb_strength, self.size, self.opacity, self.margin):
            slider.changed.connect(self._changed)
        self.position.changed.connect(self._changed)
        self.quality.valueChanged.connect(self._changed)
        self.resize_edge.valueChanged.connect(self._changed)
        bridge.preview_ready.connect(self._on_preview)
        self._load_watermark_thumb()
        self._changed()
        self.name.setFocus()
        self.name.selectAll()

    def sizeHint(self) -> QSize:
        return QSize(640, 680)

    # ------------------------------------------------------------------ form
    def current(self) -> Profile:
        """The event as the form now describes it."""
        return replace(
            self._event,
            name=self.name.text().strip(),
            destination=self.destination.currentData(),
            album=self.album.text().strip(),
            flickr_privacy=self.privacy.currentData(),
            keywords=self.keywords.text().strip(),
            hold_uploads=self.hold.isChecked(),
            auto_wb=self.auto_wb.isChecked(),
            wb_strength=self.wb_strength.value() / 100,
            auto_tone=self.auto_tone.isChecked(),
            watermark=self.watermark.isChecked(),
            watermark_path=self._watermark_path,
            watermark_position=self.position.position(),
            watermark_scale=self.size.value() / 100,
            watermark_opacity=self.opacity.value() / 100,
            watermark_margin=self.margin.value() / 100,
            max_long_edge=self.resize_edge.value(),
            jpeg_quality=self.quality.value(),
        )

    def errors(self) -> dict[str, str]:
        found = self.current().validate(self._other_names)
        if self.watermark.isChecked() and self._watermark_broken and "watermark_path" not in found:
            found["watermark_path"] = self._watermark_broken
        return found

    def _changed(self) -> None:
        event = self.current()
        uploads = event.destination != "none"
        for widget in (self.album_field, self.keywords_field, self.hold_row):
            widget.setVisible(uploads)
        self.privacy_field.setVisible(event.destination == "flickr")
        if self._album_caption is not None:
            self._album_caption.setText(
                "Gallery link" if event.destination == "smugmug" else "Album title"
            )
        self.album.setPlaceholderText(
            "https://you.smugmug.com/…/n-AbCd12"
            if event.destination == "smugmug"
            else "Created on Flickr if it does not exist"
        )
        self.wb_strength.setEnabled(event.auto_wb)
        self.watermark_box.setVisible(event.watermark)
        self.watermark_name.setText(
            self._watermark_path.replace("\\", "/").rsplit("/", 1)[-1] or "No file chosen"
        )

        found = self.errors()
        for key, line, text in (
            ("name", self.name, self.name_error),
            ("album", self.album, self.album_error),
            ("watermark_path", None, self.watermark_error),
        ):
            message = found.get(key, "")
            text.setText(message)
            text.setVisible(bool(message))
            if line is not None:
                restyle(line, invalid=bool(message))
        self.save.setEnabled(not found)
        self._debounce.start()

    def _toggle_more(self) -> None:
        opened = self.more_box.isHidden()
        self.more_box.setVisible(opened)
        self.more.set_icon("chevron-down" if opened else "chevron-right")

    # ------------------------------------------------------------- watermark
    def _choose_watermark(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose watermark", "", "PNG images (*.png)")
        if path:
            self.set_watermark_path(path)

    def set_watermark_path(self, path: str) -> None:
        self._watermark_path = path
        self._watermark_broken = ""
        self._load_watermark_thumb()
        self._changed()

    def _load_watermark_thumb(self) -> None:
        path = self._watermark_path
        if not path:
            self.watermark_thumb.clear()
            return

        def done(image: QImage | None) -> None:
            if path != self._watermark_path:
                return
            if image is None:
                self.watermark_thumb.clear()
                self._watermark_broken = "This file can't be opened. Choose a PNG image."
            else:
                self.watermark_thumb.setPixmap(QPixmap.fromImage(image))
                self._watermark_broken = ""
            self._changed()

        self.bridge.load_thumbnail(path, 32, done)

    # --------------------------------------------------------------- preview
    def _choose_sample(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose sample photo", "", "JPEG photos (*.jpg *.jpeg)"
        )
        if path:
            self.set_sample(path)

    def set_sample(self, path: str) -> None:
        self.bridge.update_settings(sample_photo=path)
        self._request_preview()

    def _request_preview(self) -> None:
        sample = self.bridge.settings.sample_photo
        if not sample:
            self.preview.set_message("Choose a sample photo to preview the look.")
            return
        self._token += 1
        event = self.current()
        if self._watermark_broken or "watermark_path" in event.validate():
            event = replace(event, watermark=False)
        self.bridge.render_preview(self._token, sample, event)

    def _on_preview(self, token: int, before: object, after: object, error: str) -> None:
        if token != self._token:
            return  # an older render; a newer one is on its way
        if isinstance(before, QImage) and isinstance(after, QImage):
            self.preview.set_images(before, after)
            self.previews_rendered += 1
        else:
            self.preview.set_message(error or "The preview failed. Choose another sample photo.")

    # ---------------------------------------------------------------- footer
    def _save(self) -> None:
        if self.errors():
            return
        self.saved = self.bridge.save_event(self.current())
        self.accept()

    def _duplicate(self) -> None:
        self.bridge.duplicate_event(self._event.id)
        self.reject()

    def _delete(self) -> None:
        if not self._delete_armed.isActive():
            self.delete.setText("Really delete?")
            self._delete_armed.start(4000)
            return
        if self.bridge.delete_event(self._event.id):
            self.reject()
        else:
            self._delete_armed.stop()
            self.delete.setText("Delete")
            self.footer_error.setText("This event is still working. Wait until it finishes.")
            self.footer_error.show()
