#!/usr/bin/env python3
"""Draw the app icon as PNGs with only the standard library. Matches static/icon.svg.

- icon-192.png, icon-512.png: the rounded icon on a clear background, for browsers.
- icon-maskable-512.png: full-bleed and opaque, with the drawing inside the middle 80% so Android's
  circle or squircle mask never cuts it.
- apple-touch-icon.png (180 px): full-bleed and opaque; iOS rounds the corners itself and would
  show clear corners as black.
"""
import struct
import sys
import zlib
from pathlib import Path

BLUE, WHITE, CLEAR = (31, 78, 121, 255), (255, 255, 255, 255), (0, 0, 0, 0)


def inside_round_rect(x, y, x0, y0, w, h, r):
    if not (x0 <= x < x0 + w and y0 <= y < y0 + h):
        return False
    cx = min(max(x, x0 + r), x0 + w - r)
    cy = min(max(y, y0 + r), y0 + h - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def pixel(u, v, opaque=False):
    """u, v in the SVG's 0-64 space. Opaque icons fill the whole square with blue instead of rounding it."""
    if not inside_round_rect(u, v, 0, 0, 64, 64, 14):
        return BLUE if opaque else CLEAR
    if 14 <= u < 50 and 31 <= v < 35:
        return BLUE
    if inside_round_rect(u, v, 14, 22, 36, 26, 4):
        return WHITE
    outer = inside_round_rect(u, v, 23, 13, 18, 13, 5)
    inner = inside_round_rect(u, v, 27, 17, 10, 5, 1)
    if outer and not inner and v < 24:
        return WHITE
    return BLUE


def png(size: int, opaque: bool = False, scale: float = 1.0) -> bytes:
    """scale < 1 shrinks the drawing toward the middle (for the maskable safe zone)."""
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            u, v = (x + 0.5) * 64 / size, (y + 0.5) * 64 / size
            row.extend(pixel(32 + (u - 32) / scale, 32 + (v - 32) / scale, opaque))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "src/jobhq/static")
    icons = {"icon-192.png": png(192), "icon-512.png": png(512),
             "icon-maskable-512.png": png(512, opaque=True, scale=0.8), "apple-touch-icon.png": png(180, opaque=True)}
    for name, data in icons.items():
        (out / name).write_bytes(data)
        print(f"wrote {name}")
