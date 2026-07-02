#!/usr/bin/env python3
"""Composite a (transparent) brain PNG onto an opaque white, rounded app-icon tile.

The dashboard's logo.png is a transparent brain — correct for the cream web
header, but a transparent macOS app icon renders on a gray plate in the Dock. For
the app icon we want the brain on a solid WHITE rounded tile. This reads a brain
PNG (RGBA, 8-bit), centers it on a 1024×1024 white rounded-rect tile, and writes
the composited master PNG. Pure stdlib (zlib) — no Pillow/ImageMagick required.

Usage: compose-icon.py <brain.png> <out-master.png> [size] [radius]
"""
import struct
import sys
import zlib


def decode_rgba(path: str) -> tuple[int, int, list[bytearray]]:
    """Decode an 8-bit RGBA (color type 6), non-interlaced PNG into rows."""
    raw = open(path, "rb").read()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    i, idat = 8, b""
    w = h = bd = ct = None
    while i < len(raw):
        ln = struct.unpack(">I", raw[i:i + 4])[0]
        typ = raw[i + 4:i + 8]
        data = raw[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct, _comp, _filt, inter = struct.unpack(">IIBBBBB", data)
            if bd != 8 or ct != 6 or inter != 0:
                raise ValueError(f"unsupported PNG (bd={bd} ct={ct} interlace={inter})")
        elif typ == b"IDAT":
            idat += data
        elif typ == b"IEND":
            break
        i += 12 + ln
    dec = zlib.decompress(idat)
    stride = w * 4
    rows: list[bytearray] = []
    prev = bytearray(stride)
    off = 0

    def paeth(a, b, c):
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)

    for _y in range(h):
        f = dec[off]; off += 1
        line = bytearray(dec[off:off + stride]); off += stride
        if f == 1:
            for x in range(4, stride): line[x] = (line[x] + line[x - 4]) & 255
        elif f == 2:
            for x in range(stride): line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - 4] if x >= 4 else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - 4] if x >= 4 else 0
                c = prev[x - 4] if x >= 4 else 0
                line[x] = (line[x] + paeth(a, prev[x], c)) & 255
        rows.append(line)
        prev = line
    return w, h, rows


def encode_rgba(w: int, h: int, rows: list[bytearray]) -> bytes:
    raw = bytearray()
    for r in rows:
        raw.append(0)          # filter type 0 (none)
        raw.extend(r)
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def rounded_coverage(x: float, y: float, w: int, h: int, r: float) -> float:
    """Anti-aliased coverage (0..1) of a rounded rectangle at pixel center."""
    # Distance from the nearest corner-arc center; only corners are rounded.
    cx = r if x < r else (w - r if x > w - r else x)
    cy = r if y < r else (h - r if y > h - r else y)
    dx, dy = x - cx, y - cy
    if dx == 0 and dy == 0:
        return 1.0
    dist = (dx * dx + dy * dy) ** 0.5
    return max(0.0, min(1.0, r - dist + 0.5))


def main() -> int:
    brain_path, out_path = sys.argv[1], sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 1024
    radius = float(sys.argv[4]) if len(sys.argv) > 4 else 0.2237 * size  # macOS squircle-ish

    bw, bh, brain = decode_rgba(brain_path)
    ox, oy = (size - bw) // 2, (size - bh) // 2  # center the brain

    out = [bytearray(size * 4) for _ in range(size)]
    for y in range(size):
        row = out[y]
        cov_y_edge = y + 0.5
        for x in range(size):
            cov = rounded_coverage(x + 0.5, cov_y_edge, size, size, radius)
            o = x * 4
            if cov <= 0.0:
                continue  # outside tile → transparent
            # Base white tile.
            r = g = b = 255
            # Composite brain (over) if this pixel falls within the brain image.
            bx, by = x - ox, y - oy
            if 0 <= bx < bw and 0 <= by < bh:
                bo = bx * 4
                br = brain[by]
                a = br[bo + 3] / 255.0
                if a > 0:
                    r = int(br[bo] * a + 255 * (1 - a) + 0.5)
                    g = int(br[bo + 1] * a + 255 * (1 - a) + 0.5)
                    b = int(br[bo + 2] * a + 255 * (1 - a) + 0.5)
            row[o] = r; row[o + 1] = g; row[o + 2] = b
            row[o + 3] = int(255 * cov + 0.5)

    open(out_path, "wb").write(encode_rgba(size, size, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
