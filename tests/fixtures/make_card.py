"""Generate a fake camera card: ``<target>/DCIM/100TEST/`` with N synthetic JPEGs.

    python tests/fixtures/make_card.py tests/fixtures/volumes/CARD_A --count 25

The card always includes an orientation-6 photo, a byte-identical duplicate, an
AppleDouble ``._`` file and an uppercase ``.JPG`` extension (SPEC §9).
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

START = datetime(2026, 5, 16, 18, 30, 0)


def synthetic_photo(index: int, size: tuple[int, int] = (480, 320)) -> Image.Image:
    """A soft gradient with a warm cast and a little noise; different for every index."""
    rng = np.random.default_rng(index)
    width, height = size
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    base = 0.25 + 0.5 * (0.6 * x + 0.4 * y) + 0.05 * np.sin((index + 1) * 3.0 * x)
    noise = rng.normal(0.0, 0.02, (height, width, 1)).astype(np.float32)
    cast = np.array([1.12, 1.0, 0.82], dtype=np.float32)  # warm venue lighting
    pixels = np.clip((base[:, :, None] + noise) * cast, 0.0, 1.0)
    return Image.fromarray((pixels * 255).astype(np.uint8), "RGB")


def save_photo(
    path: Path, index: int, orientation: int = 1, size: tuple[int, int] = (480, 320)
) -> None:
    img = synthetic_photo(index, size)
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "WeFlow Test"
    stamp = (START + timedelta(seconds=7 * index)).strftime("%Y:%m:%d %H:%M:%S")
    exif[0x0132] = stamp
    exif.get_ifd(0x8769)[0x9003] = stamp
    img.save(path, "JPEG", quality=88, exif=exif.tobytes())


def make_card(target: Path, count: int = 8, size: tuple[int, int] = (480, 320)) -> list[Path]:
    """Create the card and return its JPEG paths (``count`` files, one a duplicate)."""
    if count < 4:
        raise ValueError("count must be at least 4")
    folder = target / "DCIM" / "100TEST"
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(1, count):
        if index == 1:
            name = "IMG_0001.JPG"  # uppercase extension
        elif index == 3:
            name = "IMG_0003.jpeg"
        else:
            name = f"IMG_{index:04d}.jpg"
        save_photo(folder / name, index, orientation=6 if index == 2 else 1, size=size)
        paths.append(folder / name)
    duplicate = folder / f"IMG_{count:04d}.jpg"  # same bytes as photo 1
    shutil.copyfile(paths[0], duplicate)
    paths.append(duplicate)
    (folder / "._IMG_0001.JPG").write_bytes(b"\x00\x05\x16\x07AppleDouble resource fork")
    (folder / "IMG_0001.CR3").write_bytes(b"not a jpeg: RAW files are ignored")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--size", default="480x320", help="WIDTHxHEIGHT, e.g. 6000x4000")
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.lower().split("x"))
    paths = make_card(args.target, args.count, (width, height))
    print(f"{len(paths)} JPEGs in {args.target / 'DCIM' / '100TEST'}")


if __name__ == "__main__":
    main()
