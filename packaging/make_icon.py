"""Generate the VisionQC app icon (VQ monogram) as packaging/icon.icns.

Usage:
    python3 packaging/make_icon.py

Requires macOS `iconutil` for the final .icns step; the PNG set is written to
packaging/VisionQC.iconset/ either way.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
ICONSET = PACKAGING / "VisionQC.iconset"
ICNS = PACKAGING / "icon.icns"

CANVAS = 1024
TOP = (30, 41, 59)      # #1E293B
BOTTOM = (15, 23, 42)   # #0F172A
ACCENT = (37, 99, 235)  # #2563EB

FONT_CANDIDATES = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def render_master() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

    # Vertical gradient inside a rounded square.
    gradient = Image.new("RGB", (1, CANVAS))
    for y in range(CANVAS):
        t = y / (CANVAS - 1)
        gradient.putpixel((0, y), tuple(
            int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)
        ))
    gradient = gradient.resize((CANVAS, CANVAS))

    mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, CANVAS - 1, CANVAS - 1), radius=232, fill=255
    )
    image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)
    font = load_font(420)
    text = "VQ"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    draw.text(
        ((CANVAS - text_w) / 2 - bbox[0], (CANVAS - text_h) / 2 - bbox[1] - 40),
        text, font=font, fill=(255, 255, 255, 255),
    )

    # Accent underline.
    bar_w, bar_h = 320, 30
    draw.rounded_rectangle(
        ((CANVAS - bar_w) / 2, CANVAS * 0.72, (CANVAS + bar_w) / 2,
         CANVAS * 0.72 + bar_h),
        radius=bar_h / 2, fill=ACCENT + (255,),
    )
    return image


def main() -> int:
    master = render_master()
    ICONSET.mkdir(parents=True, exist_ok=True)

    entries = [
        (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    ]
    for size, name in entries:
        master.resize((size, size), Image.LANCZOS).save(ICONSET / name)
    print(f"wrote {len(entries)} PNGs to {ICONSET.relative_to(ROOT)}")

    if sys.platform == "darwin":
        subprocess.run(
            ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)],
            check=True,
        )
        print(f"wrote {ICNS.relative_to(ROOT)}")
    else:
        print("skipping .icns (iconutil is macOS only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
