"""Auto white balance, auto tone, resize and watermark (FR-20 – FR-25).

The colour work is a 256-entry lookup table per channel, measured on a small
thumbnail and applied to the full image in one pass.
"""

from __future__ import annotations

import contextlib
import math
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .config import Profile

Floats = NDArray[np.float32]
Reserve = Callable[[int], AbstractContextManager[None]]

ORIENTATION = 0x0112
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
GAIN_MIN, GAIN_MAX = 0.75, 1.35
GAMMA_MIN, GAMMA_MAX = 0.72, 1.35
BLACK_MAX, WHITE_MIN = 0.12, 0.80
MEDIAN_TARGET, MEDIAN_PULL = 0.46, 0.6
ANALYSIS_EDGE = 256
_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


class ProcessError(Exception):
    """A photo could not be processed. The message is shown to the user."""


# ------------------------------------------------------------------ analysis
def wb_gains(pixels: Floats, strength: float = 1.0) -> tuple[float, float, float]:
    """Gray-world gains from mid-tone, low-saturation pixels (FR-20).

    ``pixels`` is an (N, 3) array in 0..1. Gains are blended toward 1.0 by
    ``strength`` and clamped to [0.75, 1.35].
    """
    luma = pixels @ LUMA
    high = pixels.max(axis=1)
    low = pixels.min(axis=1)
    saturation = (high - low) / np.maximum(high, 1e-6)
    mask = (luma > 0.2) & (luma < 0.8) & (saturation < 0.5) & (high < 0.98)
    if int(mask.sum()) < max(50, pixels.shape[0] // 50):
        return (1.0, 1.0, 1.0)
    means = pixels[mask].mean(axis=0)
    gains = float(means.mean()) / np.maximum(means, 1e-6)
    strength = min(1.0, max(0.0, strength))
    r, g, b = (min(GAIN_MAX, max(GAIN_MIN, 1.0 + (float(gain) - 1.0) * strength)) for gain in gains)
    return (r, g, b)


def tone_params(luma: Floats) -> tuple[float, float, float]:
    """Black point, white point and gamma from luminance in 0..1 (FR-21)."""
    black = min(float(np.percentile(luma, 0.3)), BLACK_MAX)
    white = max(float(np.percentile(luma, 99.7)), WHITE_MIN)
    median = (float(np.median(luma)) - black) / (white - black)
    median = min(0.999, max(0.001, median))
    target = median + MEDIAN_PULL * (MEDIAN_TARGET - median)
    gamma = math.log(target) / math.log(median)
    return black, white, min(GAMMA_MAX, max(GAMMA_MIN, gamma))


def build_lut(
    gains: tuple[float, float, float], black: float, white: float, gamma: float
) -> list[int]:
    ramp = np.arange(256, dtype=np.float32) / 255.0
    table: list[int] = []
    for gain in gains:
        values = np.clip(ramp * gain, 0.0, 1.0)
        values = np.clip((values - black) / (white - black), 0.0, 1.0) ** gamma
        table.extend(int(v) for v in np.rint(values * 255.0))
    return table


def look_lut(img: Image.Image, profile: Profile) -> list[int] | None:
    """The lookup table for this image and event, or ``None`` when nothing changes."""
    if not (profile.auto_wb or profile.auto_tone):
        return None
    thumb = img.copy() if max(img.size) <= ANALYSIS_EDGE else img.resize(_fit(img.size))
    pixels = (np.asarray(thumb, dtype=np.float32) / 255.0).reshape(-1, 3)
    gains = wb_gains(pixels, profile.wb_strength) if profile.auto_wb else (1.0, 1.0, 1.0)
    black, white, gamma = 0.0, 1.0, 1.0
    if profile.auto_tone:
        balanced = np.clip(pixels * np.array(gains, dtype=np.float32), 0.0, 1.0)
        black, white, gamma = tone_params(balanced @ LUMA)
    return build_lut(gains, black, white, gamma)


def _fit(size: tuple[int, int], edge: int = ANALYSIS_EDGE) -> tuple[int, int]:
    scale = edge / max(size)
    return max(1, round(size[0] * scale)), max(1, round(size[1] * scale))


# ----------------------------------------------------------------- watermark
_wm_cache: dict[str, tuple[float, Image.Image]] = {}
_wm_lock = threading.Lock()


def load_watermark(path: str) -> Image.Image:
    """Load (and cache) the watermark PNG as RGBA."""
    try:
        mtime = os.stat(path).st_mtime
        with _wm_lock:
            cached = _wm_cache.get(path)
            if cached and cached[0] == mtime:
                return cached[1]
        with Image.open(path) as img:
            mark = img.convert("RGBA")
    except (OSError, ValueError, SyntaxError) as exc:
        raise ProcessError(
            "The watermark file can't be opened. Choose it again in the event settings."
        ) from exc
    with _wm_lock:
        _wm_cache[path] = (mtime, mark)
    return mark


def watermark_box(
    size: tuple[int, int], mark_size: tuple[int, int], position: str, scale: float, margin: float
) -> tuple[int, int, int, int]:
    """``(left, top, width, height)`` of the watermark; all relative to the short edge."""
    short = min(size)
    width = max(1, min(size[0], round(scale * short)))
    height = max(1, min(size[1], round(width * mark_size[1] / mark_size[0])))
    gap = round(margin * short)
    left = gap if position in ("tl", "bl") else size[0] - width - gap
    top = gap if position in ("tl", "tr") else size[1] - height - gap
    if position == "center":
        left, top = (size[0] - width) // 2, (size[1] - height) // 2
    return max(0, left), max(0, top), width, height


def apply_watermark(img: Image.Image, mark: Image.Image, profile: Profile) -> None:
    """Paste the watermark onto ``img`` in place (FR-22)."""
    left, top, width, height = watermark_box(
        img.size,
        mark.size,
        profile.watermark_position,
        profile.watermark_scale,
        profile.watermark_margin,
    )
    scaled = mark.resize((width, height), Image.Resampling.LANCZOS)
    opacity = min(1.0, max(0.0, profile.watermark_opacity))
    alpha = scaled.getchannel("A").point([round(i * opacity) for i in range(256)])
    img.paste(scaled.convert("RGB"), (left, top), alpha)


# -------------------------------------------------------------------- render
def render(img: Image.Image, profile: Profile, mark: Image.Image | None = None) -> Image.Image:
    """Apply resize, look and watermark to an upright RGB image."""
    edge = profile.max_long_edge
    if edge and max(img.size) > edge:
        img = img.resize(_fit(img.size, edge), Image.Resampling.LANCZOS)
    lut = look_lut(img, profile)
    if lut is not None:
        img = img.point(lut)
    if mark is not None:
        if lut is None and not (edge and max(img.size) > edge):
            img = img.copy()
        apply_watermark(img, mark, profile)
    return img


def open_upright(path: Path, max_edge: int = 0) -> Image.Image:
    """Open a JPEG as RGB with its EXIF orientation applied (for previews)."""
    try:
        with Image.open(path) as src:
            if max_edge:
                src.draft("RGB", (max_edge, max_edge))
            orientation = src.getexif().get(ORIENTATION, 1)
            img = src.convert("RGB")
    except (OSError, ValueError, SyntaxError) as exc:
        raise ProcessError("This photo could not be read. It may be damaged.") from exc
    if orientation in _TRANSPOSE:
        img = img.transpose(_TRANSPOSE[orientation])
    if max_edge and max(img.size) > max_edge:
        img = img.resize(_fit(img.size, max_edge), Image.Resampling.BILINEAR)
    return img


def process_file(
    source: Path, target: Path, profile: Profile, reserve: Reserve | None = None
) -> None:
    """Write the finished JPEG for ``source`` to ``target`` atomically (FR-23 – FR-25)."""
    mark = load_watermark(profile.watermark_path) if profile.watermark else None
    temp = target.with_name(f".tmp-{target.name}")
    try:
        with Image.open(source) as src, _reserved(reserve, src.size[0] * src.size[1]):
            exif = src.getexif()
            orientation = exif.get(ORIENTATION, 1)
            icc = src.info.get("icc_profile") if src.mode == "RGB" else None
            edge = profile.max_long_edge
            if edge and orientation not in _TRANSPOSE:
                src.draft("RGB", (edge, edge))
            img = src.convert("RGB") if src.mode != "RGB" else src
            if orientation in _TRANSPOSE:
                img = img.transpose(_TRANSPOSE[orientation])
            else:
                img.load()
            out = render(img, profile, mark)
            del img
            exif[ORIENTATION] = 1
            target.parent.mkdir(parents=True, exist_ok=True)
            options: dict[str, object] = {"quality": profile.jpeg_quality, "exif": exif.tobytes()}
            if icc:
                options["icc_profile"] = icc
            out.save(temp, "JPEG", **options)
        os.replace(temp, target)
    except ProcessError:
        raise
    except (OSError, ValueError, SyntaxError, MemoryError) as exc:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise ProcessError("This photo could not be processed. It may be damaged.") from exc


@contextlib.contextmanager
def _nothing() -> Iterator[None]:
    yield


def _reserved(reserve: Reserve | None, pixels: int) -> AbstractContextManager[None]:
    return reserve(pixels) if reserve else _nothing()
