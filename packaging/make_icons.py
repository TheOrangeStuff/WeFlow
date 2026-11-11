"""Draw the app icon and write the PNG, .ico and .icns files.

    python packaging/make_icons.py

Outputs: WeFlow/assets/icons/app.png, packaging/WeFlow.ico, packaging/WeFlow.icns
The mark is a memory card with a green "safe" dot, on the dark `bg` token.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BG, CARD, GOOD = "#111113", "#F4F4F5", "#22C55E"  # dark-theme bg, text and good tokens
SIZE = 1024
SCALE = 4  # supersampling for smooth edges


def draw_icon() -> Image.Image:
    s = SIZE * SCALE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    inset = int(s * 0.08)
    draw.rounded_rectangle((inset, inset, s - inset, s - inset), radius=int(s * 0.19), fill=BG)

    # Memory card: a rounded rectangle with the top-right corner cut off.
    left, top, right, bottom = int(s * 0.32), int(s * 0.24), int(s * 0.68), int(s * 0.76)
    cut = int(s * 0.11)
    stroke = int(s * 0.035)
    outline = [
        (left, top),
        (right - cut, top),
        (right, top + cut),
        (right, bottom),
        (left, bottom),
    ]
    draw.polygon(outline, fill=CARD)
    inner = [
        (left + stroke, top + stroke),
        (right - cut - stroke * 0.4, top + stroke),
        (right - stroke, top + cut + stroke * 0.4),
        (right - stroke, bottom - stroke),
        (left + stroke, bottom - stroke),
    ]
    draw.polygon(inner, fill=BG)
    for index in range(3):  # contacts
        x = left + stroke * 2.2 + index * stroke * 1.9
        draw.rounded_rectangle(
            (x, top + stroke * 2.2, x + stroke * 0.9, top + stroke * 5.2),
            radius=stroke * 0.4,
            fill=CARD,
        )

    # "Safe" dot with a tick, overlapping the card's lower-right corner.
    cx, cy, r = int(s * 0.66), int(s * 0.72), int(s * 0.135)
    draw.ellipse((cx - r - stroke, cy - r - stroke, cx + r + stroke, cy + r + stroke), fill=BG)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GOOD)
    tick = [
        (cx - r * 0.45, cy + r * 0.02),
        (cx - r * 0.12, cy + r * 0.36),
        (cx + r * 0.48, cy - r * 0.32),
    ]
    draw.line(tick, fill=BG, width=int(stroke * 1.1), joint="curve")
    for x, y in tick:
        w = stroke * 0.55
        draw.ellipse((x - w, y - w, x + w, y + w), fill=BG)
    return img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main() -> None:
    icon = draw_icon()
    png = ROOT / "WeFlow" / "assets" / "icons" / "app.png"
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(png)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.save(ROOT / "packaging" / "WeFlow.ico", sizes=sizes)
    icon.save(ROOT / "packaging" / "WeFlow.icns")
    print("wrote", png, "and packaging/WeFlow.ico, packaging/WeFlow.icns")


if __name__ == "__main__":
    main()
