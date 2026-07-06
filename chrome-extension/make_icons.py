#!/usr/bin/env python3
"""Generate PNG icons for the Second Brain Chrome extension.

No external dependencies — uses only Python stdlib (struct, zlib).
Run once from this directory: python make_icons.py
"""

import struct
import zlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Colour: #2d7a4f (the dashboard's green, matching the Import button)
R, G, B = 0x2D, 0x7A, 0x4F


def _png(size: int, r: int, g: int, b: int) -> bytes:
    """Return the bytes of a solid-colour PNG at size×size pixels."""

    def chunk(name: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + name + data
        return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

    # IHDR: width, height, bit_depth=8, colour_type=2 (RGB), compression, filter, interlace
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)

    # Image data: one scanline per row, prefixed by filter byte 0
    row = bytes([0]) + bytes([r, g, b] * size)
    raw = row * size
    idat = zlib.compress(raw, 9)

    sig = b"\x89PNG\r\n\x1a\n"
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


for size in (16, 48, 128):
    path = os.path.join(HERE, f"icon-{size}.png")
    with open(path, "wb") as f:
        f.write(_png(size, R, G, B))
    print(f"  wrote {path}")

print("Done.")
