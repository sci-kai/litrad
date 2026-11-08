#!/usr/bin/env python3
"""Draw the litresearch app icons (PNG) with the standard library only.

The icon is the favicon (static/favicon.svg): purple-to-teal gradient, white
"L", gold dot. Usage: ``tools/make_icons.py [OUTPUT-DIR]`` (default
``src/litresearch/web/static``). The PNGs are committed; run this only when the
brand changes.
"""

import struct
import sys
import zlib
from pathlib import Path

C1, C2 = (0x6C, 0x4B, 0xF4), (0x12, 0xB5, 0xA5)
WHITE, GOLD = (255, 255, 255), (0xF5, 0xA5, 0x24)
SAMPLES = 3  # supersampling per axis


def inside_rounded_square(x: float, y: float, radius: float) -> bool:
    """Point in the 64x64 square with rounded corners."""
    cx, cy = min(max(x, radius), 64 - radius), min(max(y, radius), 64 - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def logo_colour(x: float, y: float, scale: float, radius: float):
    """Colour at (x, y) of the 64x64 design, or None outside the icon.

    ``scale`` shrinks the "L" and the dot towards the centre (maskable icons
    keep them inside the safe zone); ``radius`` rounds the background.
    """
    if not inside_rounded_square(x, y, radius):
        return None
    u, v = (x - 32) / scale + 32, (y - 32) / scale + 32
    if 18 <= u <= 24 and 18 <= v <= 46 or 24 <= u <= 38 and 40 <= v <= 46:
        return WHITE
    if (u - 44) ** 2 + (v - 22) ** 2 <= 36:
        return GOLD
    t = (x + y) / 128
    return tuple(round(a + (b - a) * t) for a, b in zip(C1, C2, strict=True))


def render(size: int, scale: float = 1.0, radius: float = 16.0) -> bytes:
    """RGBA pixels, row by row, antialiased."""
    rows = []
    step = 64 / (size * SAMPLES)
    for py in range(size):
        row = bytearray([0])  # PNG filter type: none
        for px in range(size):
            acc, covered = [0, 0, 0], 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    colour = logo_colour(
                        (px * SAMPLES + sx + 0.5) * step,
                        (py * SAMPLES + sy + 0.5) * step,
                        scale,
                        radius,
                    )
                    if colour:
                        covered += 1
                        acc = [a + c for a, c in zip(acc, colour, strict=True)]
            n = SAMPLES * SAMPLES
            if covered:
                row += bytes([round(a / covered) for a in acc] + [round(255 * covered / n)])
            else:
                row += bytes(4)
        rows.append(bytes(row))
    return b"".join(rows)


def png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


ICONS = {
    "icon-192.png": (192, 1.0, 16.0),
    "icon-512.png": (512, 1.0, 16.0),
    # full-bleed, content inside the central 80 % safe zone
    "icon-maskable-512.png": (512, 0.7, 0.0),
    # iOS rounds the corners itself
    "apple-touch-icon.png": (180, 0.9, 0.0),
}


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "src/litresearch/web/static")
    for name, (size, scale, radius) in ICONS.items():
        (out / name).write_bytes(png(size, render(size, scale, radius)))
        print(out / name)


if __name__ == "__main__":
    main()
