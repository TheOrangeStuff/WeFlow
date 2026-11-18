from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms

from tests.fixtures.make_card import save_photo, synthetic_photo
from WeFlow.core import imaging
from WeFlow.core.config import Profile

PLAIN = Profile(name="plain", auto_wb=False, auto_tone=False)


def channel_means(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.float32).reshape(-1, 3).mean(axis=0)


def make_watermark(path: Path) -> Path:
    mark = Image.new("RGBA", (200, 80), (255, 0, 255, 255))
    mark.save(path)
    return path


def mark_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    pixels = np.asarray(img, dtype=np.int16)
    mask = (pixels[:, :, 0] > 200) & (pixels[:, :, 1] < 80) & (pixels[:, :, 2] > 200)
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def test_fr20_auto_wb_shifts_channel_means_toward_neutral() -> None:
    casted = synthetic_photo(1)  # warm cast: red high, blue low
    before = channel_means(casted)
    after = channel_means(imaging.render(casted, Profile(name="wb", auto_tone=False)))
    assert before[0] - before[2] > 30
    assert after[0] < before[0] - 3 and after[2] > before[2] + 3
    assert (after[0] - after[2]) < (before[0] - before[2]) * 0.7


def test_fr20_gains_blend_by_strength_and_clamp() -> None:
    pixels = np.tile(np.array([[0.8, 0.5, 0.3]], dtype=np.float32), (4000, 1))
    full = imaging.wb_gains(pixels, 1.0)
    half = imaging.wb_gains(pixels, 0.5)
    assert imaging.wb_gains(pixels, 0.0) == (1.0, 1.0, 1.0)
    assert all(imaging.GAIN_MIN <= g <= imaging.GAIN_MAX for g in full)
    assert full[2] == pytest.approx(imaging.GAIN_MAX)  # clamped
    assert 1.0 < half[2] <= full[2] and full[0] < half[0] < 1.0
    vivid = np.tile(np.array([[1.0, 0.0, 0.0]], dtype=np.float32), (4000, 1))
    assert imaging.wb_gains(vivid, 1.0) == (1.0, 1.0, 1.0)  # saturated pixels are excluded
    red_wall = np.tile(np.array([[0.8, 0.1, 0.1]], dtype=np.float32), (4000, 1))
    assert imaging.wb_gains(red_wall, 1.0) == (1.0, 1.0, 1.0)  # a red subject is not a cast


def test_fr20_strong_tungsten_cast_is_still_corrected() -> None:
    """1.10.0: under strong indoor light every neutral looks saturated; the slider must
    still have something to scale."""
    rng = np.random.default_rng(2)
    grey = rng.uniform(0.3, 0.8, (20000, 1)) * np.ones((1, 3))
    tungsten = np.clip(grey * np.array([1.0, 0.75, 0.45]), 0, 1).astype(np.float32)
    full = imaging.wb_gains(tungsten, 1.0)
    half = imaging.wb_gains(tungsten, 0.5)
    assert full[0] < 0.9 and full[2] > 1.4
    assert full[2] > half[2] > 1.0


def test_shadows_lift_and_deepen_dark_tones_only() -> None:
    ramp = np.linspace(0, 1, 256, dtype=np.float32)
    up, down = imaging.shadow_curve(ramp, 1.0), imaging.shadow_curve(ramp, -1.0)
    assert up[0] == 0 and up[-1] == pytest.approx(1.0) and down[0] == 0
    assert up[85] > ramp[85] + 0.15 and down[85] < ramp[85] - 0.05
    assert abs(up[240] - ramp[240]) < 0.01
    assert np.all(np.diff(up) >= 0) and np.all(np.diff(down) >= 0)  # never folds
    flat = Profile(name="s", auto_wb=False, auto_tone=False)
    assert imaging.look_lut(synthetic_photo(1), flat) is None
    assert imaging.look_lut(synthetic_photo(1), replace(flat, shadows=0.5)) is not None


def test_fr21_tone_params_limits_and_median_pull() -> None:
    rng = np.random.default_rng(3)
    dark = np.clip(rng.normal(0.25, 0.08, 20000), 0.0, 1.0).astype(np.float32)
    black, white, gamma = imaging.tone_params(dark)
    assert 0.0 <= black <= imaging.BLACK_MAX and white >= imaging.WHITE_MIN
    assert imaging.GAMMA_MIN <= gamma < 1.0  # brightens a dark frame
    median = (float(np.median(dark)) - black) / (white - black)
    target = median + 0.6 * (0.46 - median)
    assert median**gamma == pytest.approx(min(target, median**imaging.GAMMA_MIN), abs=1e-3)
    bright = np.clip(rng.normal(0.75, 0.05, 20000), 0.0, 1.0).astype(np.float32)
    assert 1.0 < imaging.tone_params(bright)[2] <= imaging.GAMMA_MAX
    flat = np.full(1000, 0.5, dtype=np.float32)
    assert imaging.tone_params(flat)[:2] == (imaging.BLACK_MAX, imaging.WHITE_MIN)


def test_fr21_auto_tone_brightens_dark_photo() -> None:
    dark = Image.fromarray((np.asarray(synthetic_photo(2), dtype=np.float32) * 0.4).astype("uint8"))
    out = imaging.render(dark, Profile(name="tone", auto_wb=False))
    assert channel_means(out).mean() > channel_means(dark).mean() + 3


@pytest.mark.parametrize("position", ["tl", "tr", "bl", "br", "center"])
def test_fr22_watermark_same_relative_place_portrait_and_landscape(
    tmp_path: Path, position: str
) -> None:
    mark = imaging.load_watermark(str(make_watermark(tmp_path / "mark.png")))
    profile = Profile(
        name="wm",
        auto_wb=False,
        auto_tone=False,
        watermark=True,
        watermark_position=position,
        watermark_scale=0.25,
        watermark_margin=0.05,
        watermark_opacity=1.0,
    )
    boxes = {}
    for label, size in (("landscape", (600, 400)), ("portrait", (400, 600))):
        out = imaging.render(Image.new("RGB", size, (30, 120, 30)), profile, mark)
        boxes[label] = (mark_bbox(out), size)
    (lx0, ly0, lx1, ly1), (lw, lh) = boxes["landscape"]
    (px0, py0, px1, py1), (pw, ph) = boxes["portrait"]
    assert (lx1 - lx0, ly1 - ly0) == (px1 - px0, py1 - py0) == (100, 40)  # 0.25 x short edge
    if position == "center":
        assert (lx0 + lx1) // 2 == lw // 2 and (px0 + px1) // 2 == pw // 2
        assert (ly0 + ly1) // 2 == lh // 2 and (py0 + py1) // 2 == ph // 2
    else:
        left_gap = (lx0, px0) if position in ("tl", "bl") else (lw - lx1, pw - px1)
        top_gap = (ly0, py0) if position in ("tl", "tr") else (lh - ly1, ph - py1)
        assert left_gap == (20, 20) and top_gap == (20, 20)  # 0.05 x short edge, same corner


def test_fr22_opacity_blends_watermark(tmp_path: Path) -> None:
    mark = imaging.load_watermark(str(make_watermark(tmp_path / "mark.png")))
    profile = Profile(
        name="wm", auto_wb=False, auto_tone=False, watermark=True, watermark_opacity=0.5
    )
    out = imaging.render(Image.new("RGB", (400, 400), (0, 0, 0)), profile, mark)
    assert 120 <= int(np.asarray(out)[:, :, 0].max()) <= 135


def test_fr23_orientation_exif_and_icc(tmp_path: Path) -> None:
    source, target = tmp_path / "in.jpg", tmp_path / "out" / "out.jpg"
    save_photo(source, 2, orientation=6, size=(480, 320))
    with Image.open(source) as img:
        exif = img.getexif()
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        img.save(source, "JPEG", exif=exif.tobytes(), icc_profile=icc)
    mark_path = make_watermark(tmp_path / "mark.png")
    profile = Profile(
        name="o",
        watermark=True,
        watermark_path=str(mark_path),
        watermark_opacity=1.0,
        watermark_position="br",
        auto_wb=False,
        auto_tone=False,
        jpeg_quality=95,
    )
    imaging.process_file(source, target, profile)
    with Image.open(target) as out:
        assert out.size == (320, 480)  # rotated upright
        out_exif = out.getexif()
        assert out_exif[0x0112] == 1
        assert out_exif.get_ifd(0x8769)[0x9003] == "2026:05:16 18:30:14"
        assert out_exif[0x010F] == "WeFlow Test"
        assert out.info.get("icc_profile") == icc
        x0, y0, x1, y1 = mark_bbox(out)
    # Watermark went on after rotation: bottom-right of the upright image.
    assert x1 > 320 - 20 and y1 > 480 - 20 and x0 > 160 and y0 > 240
    assert not list(target.parent.glob(".tmp-*"))  # FR-25 atomic write


def test_fr24_full_size_and_quality(tmp_path: Path) -> None:
    source = tmp_path / "in.jpg"
    save_photo(source, 4, size=(1200, 800))
    small, big = tmp_path / "small.jpg", tmp_path / "big.jpg"
    imaging.process_file(source, small, Profile(name="s", max_long_edge=600, jpeg_quality=60))
    imaging.process_file(source, big, Profile(name="b", max_long_edge=600, jpeg_quality=100))
    with Image.open(small) as img:
        assert img.size == (1200, 800)  # 1.7.0: an old resize setting is ignored
    assert small.stat().st_size < big.stat().st_size
    full = tmp_path / "full.jpg"
    imaging.process_file(source, full, PLAIN)
    with Image.open(full) as img:
        assert img.size == (1200, 800)


def test_damaged_photo_and_missing_watermark_give_plain_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8 not really a jpeg")
    with pytest.raises(imaging.ProcessError, match="damaged"):
        imaging.process_file(bad, tmp_path / "out.jpg", PLAIN)
    good = tmp_path / "good.jpg"
    save_photo(good, 1)
    missing = Profile(name="m", watermark=True, watermark_path=str(tmp_path / "nope.png"))
    with pytest.raises(imaging.ProcessError, match="watermark"):
        imaging.process_file(good, tmp_path / "out.jpg", missing)
    assert not (tmp_path / "out.jpg").exists()


def test_render_does_not_change_its_input(tmp_path: Path) -> None:
    mark = imaging.load_watermark(str(make_watermark(tmp_path / "mark.png")))
    img = Image.new("RGB", (300, 200), (10, 20, 30))
    imaging.render(img, Profile(name="w", auto_wb=False, auto_tone=False, watermark=True), mark)
    assert img.getpixel((299, 199)) == (10, 20, 30)


def test_fr27_limit_file_size_ignores_quality_and_fits(tmp_path: Path) -> None:
    source = tmp_path / "in.jpg"
    save_photo(source, 4, size=(1200, 800))
    free, limited = tmp_path / "free.jpg", tmp_path / "limited.jpg"
    imaging.process_file(source, free, Profile(name="f", jpeg_quality=100))
    limit_kb = free.stat().st_size // 3000  # about a third of the size
    event = Profile(name="l", jpeg_quality=100, limit_file_size=True, file_size_kb=limit_kb)
    imaging.process_file(source, limited, event)
    assert limited.stat().st_size <= limit_kb * 1000
    assert limited.stat().st_size > limit_kb * 1000 * 0.6  # highest quality that fits
    with Image.open(limited) as img:
        assert img.size == (1200, 800) and img.getexif().get(imaging.ORIENTATION) == 1
    assert not list(tmp_path.glob(".tmp-*"))


def test_fr27_limit_search_start_never_changes_the_answer() -> None:
    """1.14.1: starting the search from the last photo's quality only saves encodes."""
    img = synthetic_photo(4, size=(900, 600))
    sizes = {q: len(imaging.encode_jpeg(img, q, {})) for q in (5, 40, 70, 97)}
    for limit in (sizes[5], sizes[40], sizes[70] + 1, sizes[97], 10**9, 1):
        plain = imaging.encode_within(img, limit, {})
        for start in (0, 1, 39, 40, 41, 72, 99, 100):
            assert imaging.encode_within(img, limit, {}, start) == plain, (limit, start)


def test_fr27_impossible_limit_still_writes_smallest(tmp_path: Path) -> None:
    source = tmp_path / "in.jpg"
    save_photo(source, 4, size=(1200, 800))
    target, normal = tmp_path / "out.jpg", tmp_path / "normal.jpg"
    limited = Profile(name="t", auto_wb=False, auto_tone=False, limit_file_size=True)
    limited.file_size_kb = 10
    imaging.process_file(source, target, limited)
    imaging.process_file(source, normal, PLAIN)
    with Image.open(source) as img:
        lowest = imaging.encode_jpeg(img.convert("RGB"), 0, {})
    assert len(lowest) > 10_000  # the limit really cannot be met
    assert target.is_file() and target.stat().st_size < normal.stat().st_size / 3


def test_fr27_quality_accepts_full_lightroom_range(tmp_path: Path) -> None:
    source = tmp_path / "in.jpg"
    save_photo(source, 4, size=(600, 400))
    low = tmp_path / "low.jpg"
    imaging.process_file(source, low, Profile(name="q", jpeg_quality=5))
    assert Profile(name="q", jpeg_quality=0).validate() == {}
    assert low.stat().st_size < source.stat().st_size


def test_fr21b_highlights_pull_down_and_brighten_bright_tones_only() -> None:
    ramp = np.linspace(0, 1, 256, dtype=np.float32)
    down, up = imaging.highlight_curve(ramp, -1.0), imaging.highlight_curve(ramp, 1.0)
    assert down[0] == 0 and down[-1] == pytest.approx(1.0) and up[-1] == pytest.approx(1.0)
    assert down[170] < ramp[170] - 0.15 and up[170] > ramp[170] + 0.05
    assert abs(down[15] - ramp[15]) < 0.01
    assert np.all(np.diff(up) >= 0) and np.all(np.diff(down) >= 0)  # never folds
    flat = Profile(name="h", auto_wb=False, auto_tone=False)
    assert imaging.look_lut(synthetic_photo(1), replace(flat, highlights=-0.5)) is not None
