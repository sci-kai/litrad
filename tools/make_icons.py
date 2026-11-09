#!/usr/bin/env python3
"""Render the litrad app icons (PNG) from the logo, static/favicon.svg.

The logo is a hand-written SVG: a rat with a black mortarboard and a gold
tassel on the purple-to-teal tile of the app, with faint radar arcs. Its parts
carry ids: ``tile`` (the rounded background), ``radar`` and ``rat``. The icons
are variants of it:

- ``icon-192.png``, ``icon-512.png``: the logo as it is (rounded corners,
  transparent outside the tile); ``icon-512.png`` is also the desktop app icon;
- ``icon-maskable-512.png``: full-bleed tile, the rat scaled into the central
  safe zone (Android cuts the icon to a circle or a squircle);
- ``apple-touch-icon.png`` (180): full-bleed tile, iOS rounds the corners.

There is no SVG library in the standard library, so headless Firefox draws
them: all icons on one page at their exact pixel size, screenshot once on a
black and once on a white background (fresh profile, 1 CSS px = 1 device px).
The two screenshots give every pixel's colour and transparency exactly
(alpha = 1 - (white - black) / 255), so the PNGs have real alpha channels.
The PNG reading and writing below uses only ``zlib`` and ``struct``.

Usage: ``tools/make_icons.py [OUTPUT-DIR]`` (default ``src/litrad/web/static``);
``--firefox PATH`` chooses the browser. The PNGs are committed; run this only
when the logo changes. The same Firefox version gives the same files.
"""

import argparse
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "src" / "litrad" / "web" / "static"
LOGO = STATIC / "favicon.svg"

# name: (size in px, corner radius of the tile in logo units (of 64), scale of the rat)
ICONS = {
    "icon-192.png": (192, 16, 1.0),
    "icon-512.png": (512, 16, 1.0),
    # Android's safe zone is the central circle of 80 % diameter
    "icon-maskable-512.png": (512, 0, 0.74),
    "apple-touch-icon.png": (180, 0, 0.9),
}
GAP = 8  # px between the icons on the page


# --- SVG variants -------------------------------------------------------------------------


def variant(svg: str, radius: float, scale: float) -> str:
    """The logo with another tile radius and the rat scaled around the centre."""
    svg, found = re.subn(r'(<rect id="tile"[^>]*\brx=")[^"]*(")', rf"\g<1>{radius:g}\2", svg)
    if found != 1:
        raise SystemExit(f'{LOGO}: expected one <rect id="tile" rx="...">')
    if scale != 1:
        opening = '<g id="rat"'
        if svg.count(opening) != 1:
            raise SystemExit(f'{LOGO}: expected one <g id="rat">')
        start = svg.index(opening)
        end = _closing_g(svg, start)
        transform = f'<g transform="translate(32 32) scale({scale:g}) translate(-32 -32)">'
        svg = svg[:start] + transform + svg[start:end] + "</g>" + svg[end:]
    return svg


def _closing_g(svg: str, start: int) -> int:
    """Index just after the ``</g>`` that closes the group opened at ``start``."""
    depth = 0
    for match in re.finditer(r"<g\b|</g>", svg[start:]):
        depth += 1 if match.group() != "</g>" else -1
        if depth == 0:
            return start + match.end()
    raise SystemExit(f"{LOGO}: unclosed <g>")


def page(svgs: list[tuple[str, int]], background: str) -> tuple[str, list[int], int, int]:
    """HTML with the SVGs side by side at exact sizes; (html, x offsets, width, height)."""
    xs, x = [], 0
    for _, size in svgs:
        xs.append(x)
        x += size + GAP
    width, height = x, max(size for _, size in svgs)
    parts = []
    for (svg, size), left in zip(svgs, xs, strict=True):
        inline = re.sub(r"<svg\b", f'<svg width="{size}" height="{size}"', svg, count=1)
        parts.append(f'<div style="position:absolute;left:{left}px;top:0">{inline}</div>')
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        f"html,body{{margin:0;padding:0;overflow:hidden;background:{background}}}"
        "svg{display:block}</style></head><body>" + "".join(parts) + "</body></html>"
    )
    return html, xs, width, height


# --- Firefox --------------------------------------------------------------------------------


def screenshot(firefox: str, html: str, width: int, height: int, work: Path, name: str) -> Path:
    """A screenshot of ``html`` at ``width`` x ``height`` CSS (= device) pixels."""
    source = work / f"{name}.html"
    source.write_text(html, encoding="utf-8")
    profile = work / f"profile-{name}"  # fresh: no extensions, zoom or scaling of the user
    profile.mkdir()
    (profile / "user.js").write_text(
        'user_pref("layout.css.devPixelsPerPx", "1.0");\n'
        'user_pref("browser.shell.checkDefaultBrowser", false);\n'
        'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);\n',
        encoding="utf-8",
    )
    target = work / f"{name}.png"
    command = [
        firefox,
        "--headless",
        "--no-remote",
        "--profile",
        str(profile),
        "--screenshot",
        str(target),
        f"--window-size={width},{height}",
        source.as_uri(),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=300)
    if not target.is_file():
        raise SystemExit(f"Firefox wrote no screenshot ({' '.join(command)})")
    return target


# --- PNG ------------------------------------------------------------------------------------


def read_png(path: Path) -> tuple[int, int, list[bytearray]]:
    """(width, height, rows of RGBA bytes) of an 8-bit RGB/RGBA, non-interlaced PNG."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path} is not a PNG")
    pos, idat, header = 8, b"", None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or colour not in (2, 6) or interlace:
        raise SystemExit(f"{path}: unsupported PNG (depth {depth}, colour type {colour})")
    channels = 4 if colour == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * channels
    rows, previous = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                pred = left if pa <= pb and pa <= pc else up if pb <= pc else corner
                line[i] = (line[i] + pred) & 255
        previous = line
        if channels == 3:
            line = bytearray(b for j in range(width) for b in (*line[3 * j : 3 * j + 3], 255))
        rows.append(line)
    return width, height, rows


def write_png(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + body))
        return struct.pack(">I", len(body)) + kind + body + crc

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    raw = b"".join(b"\x00" + bytes(row) for row in rows)  # filter type: none
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def unmatte(black: list[bytearray], white: list[bytearray], x0: int, size: int) -> list[bytes]:
    """RGBA rows of the square at ``x0`` from its renderings on black and on white."""
    rows = []
    for y in range(size):
        b_row, w_row, out = black[y], white[y], bytearray()
        for x in range(x0, x0 + size):
            b, w = b_row[4 * x : 4 * x + 3], w_row[4 * x : 4 * x + 3]
            alpha = 255 - round(sum(wc - bc for bc, wc in zip(b, w, strict=True)) / 3)
            alpha = min(max(alpha, 0), 255)
            if alpha == 0:
                out += bytes(4)
                continue
            out += bytes(min(255, round(bc * 255 / alpha)) for bc in b) + bytes([alpha])
        rows.append(bytes(out))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output", nargs="?", type=Path, default=STATIC)
    parser.add_argument("--firefox", default=shutil.which("firefox") or "firefox")
    args = parser.parse_args()
    logo = LOGO.read_text(encoding="utf-8")
    svgs = [(variant(logo, radius, scale), size) for size, radius, scale in ICONS.values()]
    with tempfile.TemporaryDirectory(prefix="litrad-icons-") as folder:
        work = Path(folder)
        shots = {}
        for background in ("black", "white"):
            html, xs, width, height = page(svgs, background)
            shot = screenshot(args.firefox, html, width, height, work, background)
            shot_width, _, rows = read_png(shot)
            if shot_width < width or len(rows) < height:
                raise SystemExit(f"Screenshot too small: {shot_width} x {len(rows)}")
            shots[background] = rows
    args.output.mkdir(parents=True, exist_ok=True)
    for (name, (size, _, _)), x0 in zip(ICONS.items(), xs, strict=True):
        rows = unmatte(shots["black"], shots["white"], x0, size)
        write_png(args.output / name, size, size, rows)
        print(args.output / name)


if __name__ == "__main__":
    sys.exit(main())
