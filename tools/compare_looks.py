"""Render side-by-side before/after JPEGs to judge the Look settings on real photos.

    python tools/compare_looks.py ~/R1-samples ~/R1-compare --shadows 20 --highlights -30

Reads every JPEG in the source folder (never changes it) and writes one wide JPEG per
photo to the output folder: the camera original on the left, WeFlow's look on the right.
Not part of the app or its builds.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from PIL import Image

from WeFlow.core import imaging
from WeFlow.core.config import Profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--shadows", type=int, default=0, help="-100..100")
    parser.add_argument("--highlights", type=int, default=0, help="-100..100")
    parser.add_argument("--no-auto-tone", action="store_true")
    parser.add_argument("--no-auto-wb", action="store_true")
    parser.add_argument("--edge", type=int, default=1600, help="long edge of each half")
    args = parser.parse_args()

    profile = replace(
        Profile(name="compare"),
        auto_tone=not args.no_auto_tone,
        auto_wb=not args.no_auto_wb,
        shadows=args.shadows / 100,
        highlights=args.highlights / 100,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    photos = sorted(
        p
        for p in args.source.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg"} and not p.name.startswith(".")
    )
    for path in photos:
        try:
            before = imaging.open_upright(path, args.edge)
        except imaging.ProcessError as exc:
            print(f"{path.name}: skipped. {exc}")
            continue
        after = imaging.render(before.copy(), profile)
        pair = Image.new("RGB", (before.width * 2, before.height))
        pair.paste(before, (0, 0))
        pair.paste(after, (before.width, 0))
        pair.save(args.output / f"{path.stem}_compare.jpg", quality=90)
        print(path.name)


if __name__ == "__main__":
    main()
