"""Design tokens (SPEC §7.5) → QSS, icons and live light/dark switching.

Widgets never hard-code a colour: they ask ``theme.color("good")`` or rely on
the generated stylesheet.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#F7F7F8",
    "surface": "#FFFFFF",
    "surface-2": "#F0F0F2",
    "border": "#E4E4E7",
    "text": "#111114",
    "text-2": "#6B6B73",
    "accent": "#157A3A",  # 1.14.0: green, as in the new main screen
    "good": "#16A34A",
    "warn": "#D97706",
    "bad": "#DC2626",
}
DARK = {
    "bg": "#111113",
    "surface": "#1A1A1D",
    "surface-2": "#232327",
    "border": "#2E2E33",
    "text": "#F4F4F5",
    "text-2": "#A1A1AA",
    "accent": "#1DB954",
    "good": "#22C55E",
    "warn": "#F59E0B",
    "bad": "#F87171",
}
TINT_ALPHA = {"light": 0.12, "dark": 0.16}
STATUSES = ("good", "warn", "bad")
SPACING = (4, 8, 12, 16, 24, 32)
RADIUS_CONTROL, RADIUS_CARD, RADIUS_PANEL = 8, 12, 16
CONTROL_HEIGHT = 32
TYPE = {  # name: (px, weight, colour token)
    "display": (28, 600, "text"),
    "title": (17, 600, "text"),
    "heading": (15, 600, "text"),
    "body": (13, 400, "text"),
    "secondary": (12, 400, "text-2"),
    "label": (11, 500, "text-2"),
    # 1.14.0: the main screen (DECISIONS 1.14.0)
    "brand": (26, 800, "text"),
    "brand-mark": (26, 900, "accent"),
    "hero": (56, 800, "text"),
    "hero-sub": (20, 400, "text-2"),
    "dest": (20, 700, "text"),
    "dest-sub": (14, 400, "text-2"),
    "stage": (22, 600, "text"),
}
ITALIC = ("brand", "brand-mark", "hero")
ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
AA_TEXT = 4.5


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def mix(top: str, base: str, alpha: float) -> str:
    """``top`` at ``alpha`` opacity over ``base``."""
    t, b = _rgb(top), _rgb(base)
    return "#{:02X}{:02X}{:02X}".format(*(round(b[i] + (t[i] - b[i]) * alpha) for i in range(3)))


def luminance(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _readable(colour: str, toward: str, backgrounds: list[str]) -> str:
    """Nudge ``colour`` toward ``toward`` until it reads as text on every background."""
    for step in range(21):
        candidate = mix(toward, colour, step / 20)
        if all(contrast(candidate, bg) >= AA_TEXT for bg in backgrounds):
            return candidate
    return toward


def tokens(scheme: str) -> dict[str, str]:
    """All colour tokens for ``"light"`` or ``"dark"``, including derived ones."""
    t = dict(DARK if scheme == "dark" else LIGHT)
    for status in STATUSES:
        t[f"{status}-tint"] = mix(t[status], t["surface"], TINT_ALPHA[scheme])
    for status in STATUSES:
        # Status colour that is readable as text on its own tint and on neutral surfaces.
        grounds = [t[f"{status}-tint"], t["surface-2"], t["bg"]]
        t[f"{status}-strong"] = _readable(t[status], t["text"], grounds)
    # White on the dark-theme accent fails AA, so the label colour follows the theme.
    t["on-accent"] = "#FFFFFF" if contrast("#FFFFFF", t["accent"]) >= AA_TEXT else t["bg"]
    t["accent-hover"] = mix(t["text"], t["accent"], 0.12)
    t["hover"] = mix(t["text"], t["surface-2"], 0.06)
    t["shadow-1"] = "rgba(0,0,0,0.06)" if scheme == "light" else "none"
    return t


def font_families() -> list[str]:
    if sys.platform == "darwin":
        return ["SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue"]
    if sys.platform == "win32":
        return ["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"]
    return []


def build_qss(t: dict[str, str], arrow: str = "") -> str:
    """The whole application stylesheet for one set of tokens. ``arrow`` is the
    path of the dropdown chevron drawn in this scheme's colours (see ``write_arrows``)."""
    arrow_rule = f"image: url({arrow}); " if arrow else ""
    type_rules = "\n".join(
        f'QLabel[type="{name}"] {{ font-size: {px}px; font-weight: {weight}; '
        f"color: {t[colour]}; background: transparent; }}"
        for name, (px, weight, colour) in TYPE.items()
    )
    type_rules += "\n" + "\n".join(f'QLabel[type="{n}"] {{ font-style: italic; }}' for n in ITALIC)
    panel_rules = "\n".join(
        f'QFrame#StatusPanel[tone="{tone}"] {{ background: transparent; border: none; }}'
        for tone in ("neutral", "warn", "good", "good-edge", "bad", "bad-edge")
    )
    chip_rules = "\n".join(
        f'QFrame#Chip[tone="{tone}"] {{ background: {t[bg]}; border-radius: 11px; }}\n'
        f'QFrame#Chip[tone="{tone}"] QLabel {{ color: {t[fg]}; font-size: 11px; '
        f"font-weight: 500; background: transparent; }}"
        for tone, bg, fg in (
            ("good", "good-tint", "good-strong"),
            ("warn", "warn-tint", "warn-strong"),
            ("bad", "bad-tint", "bad-strong"),
            ("neutral", "surface-2", "text-2"),
        )
    )
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t["text"]}; font-size: 13px; }}
QMainWindow, QDialog, QWidget#Page {{ background: {t["bg"]}; }}
QLabel {{ background: transparent; }}
{type_rules}
QLabel[tinted="true"] {{ color: {t["text"]}; }}

QLabel[type="error"] {{ font-size: 12px; color: {t["bad-strong"]}; }}
QLabel[type="thumb"] {{ background: {t["border"]}; border-radius: 6px; }}

QFrame#EventCard {{ background: transparent; }}
QFrame#Card {{ background: {t["surface"]}; border: 1px solid {t["border"]};
    border-radius: {RADIUS_CARD}px; }}
QFrame#NoticeBar {{ background: {t["surface"]}; border: 1px solid {t["border"]};
    border-radius: {RADIUS_CARD}px; }}
QFrame#Drawer {{ background: {t["surface"]}; border-left: 1px solid {t["border"]}; }}
QFrame#StageSeparator, QFrame#VSeparator {{ background: {t["border"]}; border: none; }}
QFrame#Segmented {{ background: transparent; border: 1px solid {t["text-2"]};
    border-radius: 18px; }}
QPushButton[kind="segment"] {{ min-height: 34px; max-height: 34px; padding: 0 20px;
    border-radius: 17px; border: none; background: transparent; color: {t["text"]};
    font-size: 15px; font-weight: 600; }}
QPushButton[kind="segment"]:hover {{ background: {t["hover"]}; }}
QPushButton[kind="segment"]:checked {{ background: {t["accent"]}; color: {t["on-accent"]}; }}
QPushButton[kind="segment"]:focus {{ border: 2px solid {t["accent"]}; padding: 0 18px;
    min-height: 30px; max-height: 30px; }}
QPushButton[kind="segment"]:checked:focus {{ border: 2px solid {t["text"]}; }}
QFrame#Separator {{ background: {t["border"]}; max-height: 1px; min-height: 1px; border: none; }}
{panel_rules}
{chip_rules}

QPushButton {{ min-height: {CONTROL_HEIGHT - 2}px; max-height: {CONTROL_HEIGHT - 2}px;
    padding: 0 12px; border-radius: {RADIUS_CONTROL}px; border: 1px solid {t["border"]};
    background: {t["surface"]}; color: {t["text"]}; font-size: 13px; font-weight: 500; }}
QPushButton:hover {{ background: {t["surface-2"]}; }}
QPushButton:focus {{ border: 2px solid {t["accent"]}; padding: 0 11px;
    min-height: {CONTROL_HEIGHT - 4}px; max-height: {CONTROL_HEIGHT - 4}px; }}
QPushButton:disabled {{ color: {t["text-2"]}; background: {t["surface-2"]};
    border-color: {t["surface-2"]}; }}
QPushButton[kind="primary"] {{ background: {t["accent"]}; color: {t["on-accent"]};
    border: 1px solid {t["accent"]}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t["accent-hover"]};
    border-color: {t["accent-hover"]}; }}
QPushButton[kind="primary"]:focus {{ border: 2px solid {t["text"]}; }}
QPushButton[kind="primary"]:disabled {{ background: {t["border"]}; color: {t["text-2"]};
    border-color: {t["border"]}; }}
QPushButton[kind="text"] {{ background: transparent; border: 1px solid transparent;
    color: {t["accent"]}; padding: 0 8px; }}
QPushButton[kind="text"]:hover {{ background: {t["hover"]}; }}
QPushButton[kind="text"]:focus {{ border: 2px solid {t["accent"]}; padding: 0 7px; }}
QPushButton[kind="quiet"] {{ background: transparent; border: 1px solid transparent;
    color: {t["text-2"]}; font-size: 12px; font-weight: 400; padding: 0 8px; }}
QPushButton[kind="quiet"]:hover {{ color: {t["text"]}; background: {t["hover"]}; }}
QPushButton[kind="quiet"]:focus {{ border: 2px solid {t["accent"]}; padding: 0 7px; }}
QPushButton[kind="link"] {{ background: transparent; border: 1px solid transparent;
    color: {t["text-2"]}; font-size: 12px; font-weight: 400; padding: 0 2px;
    text-decoration: underline; }}
QPushButton[kind="link"]:hover {{ color: {t["text"]}; background: transparent; }}
QPushButton[kind="link"]:focus {{ border: 2px solid {t["accent"]}; padding: 0 1px; }}
QPushButton[kind="outline"] {{ background: transparent; border: 1px solid {t["text-2"]};
    color: {t["text"]}; }}
QPushButton[kind="outline"]:hover {{ background: {t["hover"]}; }}
QPushButton[kind="outline"]:focus {{ border: 2px solid {t["accent"]}; }}
QPushButton[kind="good"] {{ background: transparent; border: 1px solid {t["good"]};
    color: {t["good-strong"]}; font-weight: 600; }}
QPushButton[kind="good"]:hover {{ background: {t["good-tint"]}; }}
QPushButton[kind="good"]:focus {{ border: 2px solid {t["accent"]}; }}
QPushButton[kind="icon"] {{ background: transparent; border: 1px solid transparent;
    padding: 0; min-width: {CONTROL_HEIGHT - 2}px; max-width: {CONTROL_HEIGHT - 2}px; }}
QPushButton[kind="icon"]:hover {{ background: {t["hover"]}; }}
QPushButton[kind="icon"]:focus {{ border: 2px solid {t["accent"]}; padding: 0;
    min-width: {CONTROL_HEIGHT - 4}px; max-width: {CONTROL_HEIGHT - 4}px; }}
QPushButton[kind="cell"] {{ min-width: 26px; max-width: 26px; min-height: 26px;
    max-height: 26px; padding: 0; border-radius: 6px; background: {t["surface"]};
    border: 1px solid {t["border"]}; }}
QPushButton[kind="cell"]:checked {{ background: {t["accent"]}; border-color: {t["accent"]}; }}
QPushButton[kind="cell"]:disabled {{ background: {t["surface-2"]};
    border-color: {t["surface-2"]}; }}
QPushButton[kind="cell"]:focus {{ border: 2px solid {t["accent"]}; padding: 0;
    min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; }}
QPushButton[scale="large"] {{ min-height: 48px; max-height: 48px; padding: 0 28px;
    font-size: 20px; font-weight: 700; }}
QPushButton[scale="large"]:focus {{ min-height: 46px; max-height: 46px; padding: 0 27px; }}
QPushButton[scale="medium"] {{ min-height: 38px; max-height: 38px; padding: 0 16px;
    font-size: 15px; font-weight: 600; }}
QPushButton[scale="medium"]:focus {{ min-height: 36px; max-height: 36px; padding: 0 15px; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QComboBox, QSpinBox {{ min-height: {CONTROL_HEIGHT - 2}px;
    max-height: {CONTROL_HEIGHT - 2}px; padding: 0 8px; border-radius: {RADIUS_CONTROL}px;
    border: 1px solid {t["border"]}; background: {t["surface"]}; color: {t["text"]};
    selection-background-color: {t["accent"]}; selection-color: {t["on-accent"]}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {t["accent"]};
    padding: 0 7px; min-height: {CONTROL_HEIGHT - 4}px; max-height: {CONTROL_HEIGHT - 4}px; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {t["text-2"]};
    background: {t["surface-2"]}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {t["bad"]}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ {arrow_rule}width: 12px; height: 12px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QComboBox QAbstractItemView, QListWidget#GalleryList {{ background: {t["surface"]};
    border: 1px solid {t["border"]};
    border-radius: {RADIUS_CONTROL}px; padding: 4px; outline: none;
    selection-background-color: {t["surface-2"]}; selection-color: {t["text"]}; }}
QListWidget#GalleryList::item {{ min-height: 28px; padding: 0 8px; border-radius: 6px; }}
QListWidget#GalleryList::item:selected {{ background: {t["surface-2"]}; color: {t["text"]}; }}

QSlider {{ min-height: 24px; max-height: 24px; }}
QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {t["border"]}; }}
QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {t["accent"]}; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: {t["surface"]}; border: 1px solid {t["text-2"]}; }}
QSlider::handle:horizontal:focus {{ border: 2px solid {t["accent"]}; }}
QSlider::handle:horizontal:disabled {{ border-color: {t["border"]}; }}
QSlider::sub-page:horizontal:disabled {{ background: {t["border"]}; }}

QMenu {{ background: {t["surface"]}; border: 1px solid {t["border"]};
    border-radius: {RADIUS_CONTROL}px; padding: 4px; }}
QMenu::item {{ padding: 8px 24px 8px 12px; border-radius: 6px; color: {t["text"]}; }}
QMenu::item:selected {{ background: {t["surface-2"]}; }}
QMenu::item:disabled {{ color: {t["text-2"]}; }}
QMenu::separator {{ height: 1px; background: {t["border"]}; margin: 4px 8px; }}
QMenu::icon {{ padding-left: 8px; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t["border"]}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    height: 0; background: transparent; }}
QPlainTextEdit {{ background: transparent; border: none; color: {t["text"]}; font-size: 12px; }}
QToolTip {{ background: {t["surface"]}; color: {t["text"]}; border: 1px solid {t["border"]};
    padding: 4px 8px; }}
"""


class Theme(QObject):
    """The current tokens. Emits ``changed`` when the OS switches light/dark."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.scheme = "light"
        self.t = tokens("light")
        self._icons: dict[tuple[str, str, int, str, float, bool], QPixmap] = {}
        self._sources: dict[str, str] = {}
        self.arrows: dict[str, str] = {}  # scheme -> chevron file for dropdowns

    def color(self, token: str) -> QColor:
        return QColor(self.t[token])

    def set_scheme(self, scheme: str) -> None:
        scheme = "dark" if scheme == "dark" else "light"
        self.scheme = scheme
        self.t = tokens(scheme)
        self._icons.clear()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setPalette(self._palette())
            app.setStyleSheet(build_qss(self.t, self.arrows.get(scheme, "")))
        self.changed.emit()

    def _palette(self) -> QPalette:
        palette = QPalette()
        roles = {
            QPalette.ColorRole.Window: "bg",
            QPalette.ColorRole.Base: "surface",
            QPalette.ColorRole.AlternateBase: "surface-2",
            QPalette.ColorRole.Button: "surface",
            QPalette.ColorRole.WindowText: "text",
            QPalette.ColorRole.Text: "text",
            QPalette.ColorRole.ButtonText: "text",
            QPalette.ColorRole.PlaceholderText: "text-2",
            QPalette.ColorRole.Highlight: "accent",
            QPalette.ColorRole.HighlightedText: "on-accent",
            QPalette.ColorRole.ToolTipBase: "surface",
            QPalette.ColorRole.ToolTipText: "text",
        }
        for role, token in roles.items():
            palette.setColor(role, self.color(token))
        return palette

    def pixmap(
        self,
        name: str,
        token: str = "text",
        size: int = 16,
        ratio: float = 2.0,
        stroke: float = 1.5,
        filled: bool = False,
    ) -> QPixmap:
        """A bundled line icon drawn in a token colour with a ``stroke`` px line;
        ``filled`` also fills its shapes (the big pause and play glyphs)."""
        key = (name, token, size, self.scheme, stroke, filled)
        cached = self._icons.get(key)
        if cached is not None:
            return cached
        svg = self._svg(name)
        if filled:
            svg = svg.replace('fill="none"', 'fill="currentColor"')
        svg = svg.replace("currentColor", self.t[token])
        svg = svg.replace('stroke-width="2"', f'stroke-width="{stroke * 24 / size:.2f}"')
        pixmap = QPixmap(QSize(round(size * ratio), round(size * ratio)))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(
            painter, QRectF(0, 0, size * ratio, size * ratio)
        )
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        self._icons[key] = pixmap
        return pixmap

    def _svg(self, name: str) -> str:
        if name not in self._sources:
            try:
                self._sources[name] = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
            except OSError:
                self._sources[name] = (
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"/>'
                )
        return self._sources[name]

    def icon(self, name: str, token: str = "text", size: int = 16, filled: bool = False) -> QIcon:
        return QIcon(self.pixmap(name, token, size, filled=filled))


theme = Theme()


def preload_icons() -> None:
    """Read every bundled icon once at start-up, so painting never touches the disk."""
    for path in ICON_DIR.glob("*.svg"):
        theme._svg(path.stem)


def write_arrows() -> None:
    """Stylesheets take images only from files, so write the dropdown chevron in each
    scheme's secondary text colour once, at start-up, before any window opens."""
    folder = Path(tempfile.gettempdir()) / "WeFlow-icons"
    try:
        folder.mkdir(exist_ok=True)
        for scheme in ("light", "dark"):
            svg = theme._svg("chevron-down").replace("currentColor", tokens(scheme)["text-2"])
            path = folder / f"chevron-down-{scheme}.svg"
            path.write_text(svg, encoding="utf-8")
            theme.arrows[scheme] = path.as_posix()
    except OSError:
        theme.arrows.clear()  # no arrow is better than no app


def system_scheme(app: QApplication) -> str:
    forced = os.environ.get("WeFlow_THEME", "").lower()
    if forced in ("light", "dark"):
        return forced
    return "dark" if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"


def install(app: QApplication) -> None:
    """Apply the theme now and keep following the OS light/dark setting."""
    app.setStyle("Fusion")
    font = QFont(app.font())
    available = set(QFontDatabase.families())
    family = next((f for f in font_families() if f in available), None)
    if family:
        font.setFamily(family)
    font.setPixelSize(13)
    app.setFont(font)
    write_arrows()
    theme.set_scheme(system_scheme(app))
    app.styleHints().colorSchemeChanged.connect(
        lambda _scheme: theme.set_scheme(system_scheme(app))
    )
