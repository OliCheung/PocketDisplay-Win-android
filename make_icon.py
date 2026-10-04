"""Generate the PocketDisplay app icon (assets/icon.ico + assets/icon.png).

NOTE: this is the older hand-drawn variant. The current icons are rendered from
the Android app's adaptive icon instead — see make_icon_android.ps1 (rasterize
the VectorDrawable) + make_icon_ico.py (pack the ICO). Kept only as a fallback
if the Android resources are unavailable.

Pure standard library: the icon is drawn procedurally (a phone/screen with a
cast glyph) and encoded as PNG/ICO by hand, so no Pillow is required.
"""

import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")

BODY = (43, 58, 85)      # dark slate blue — device body
SCREEN = (86, 196, 239)  # light blue — screen
GLYPH = (255, 255, 255)  # white — cast glyph


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def sd_round_rect(px, py, cx, cy, hw, hh, r):
    """Signed distance to a rounded rectangle (negative inside)."""
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    return outside + inside - r


def sd_convex(px, py, pts, centroid):
    """Signed distance to a convex polygon via edge half-planes."""
    d = -1e30
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        ex, ey = x2 - x1, y2 - y1
        nx, ny = ey, -ex
        if (centroid[0] - x1) * nx + (centroid[1] - y1) * ny > 0:
            nx, ny = -nx, -ny
        d = max(d, (px - x1) * nx + (py - y1) * ny)
    return d


def coverage(sd):
    return clamp(0.5 - sd)


def render(size, ss=None):
    """Render the icon at `size` px, RGBA, anti-aliased by supersampling."""
    if ss is None:
        ss = 4 if size <= 64 else 2
    S = size * ss
    buf = bytearray(S * S * 4)

    tri = [(0.47, 0.37), (0.47, 0.63), (0.66, 0.50)]
    centroid = (sum(p[0] for p in tri) / 3.0, sum(p[1] for p in tri) / 3.0)
    # The SDFs work in normalized units; coverage needs them in pixels,
    # otherwise the anti-aliasing ramp is half the icon wide (a fuzzy blob).
    px_scale = float(S)

    for y in range(S):
        fy = (y + 0.5) / S
        row = y * S * 4
        for x in range(S):
            fx = (x + 0.5) / S
            c_body = coverage(sd_round_rect(fx, fy, 0.5, 0.5, 0.30, 0.44, 0.07) * px_scale)
            c_screen = coverage(sd_round_rect(fx, fy, 0.5, 0.5, 0.225, 0.335, 0.045) * px_scale)
            c_glyph = coverage(sd_convex(fx, fy, tri, centroid) * px_scale)

            r = g = b = 0.0
            a = 0.0
            for col, sa in ((BODY, c_body), (SCREEN, c_screen), (GLYPH, c_glyph)):
                if sa <= 0.0:
                    continue
                r = col[0] * sa + r * (1.0 - sa)
                g = col[1] * sa + g * (1.0 - sa)
                b = col[2] * sa + b * (1.0 - sa)
                a = sa + a * (1.0 - sa)

            i = row + x * 4
            # r/g/b are 0-255 floats; a is a 0..1 coverage composite and must
            # be scaled to 0-255 (the old code clamped everything to 1.0,
            # producing an all-black, fully-transparent icon).
            buf[i] = max(0, min(255, int(r + 0.5)))
            buf[i + 1] = max(0, min(255, int(g + 0.5)))
            buf[i + 2] = max(0, min(255, int(b + 0.5)))
            buf[i + 3] = max(0, min(255, int(a * 255.0 + 0.5)))

    # box-downsample the supersampled image
    out = bytearray(size * size * 4)
    inv = 1.0 / (ss * ss)
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0
            for dy in range(ss):
                srow = (y * ss + dy) * S
                for dx in range(ss):
                    si = (srow + x * ss + dx) * 4
                    r += buf[si]
                    g += buf[si + 1]
                    b += buf[si + 2]
                    a += buf[si + 3]
            oi = (y * size + x) * 4
            out[oi] = int(r * inv + 0.5)
            out[oi + 1] = int(g * inv + 0.5)
            out[oi + 2] = int(b * inv + 0.5)
            out[oi + 3] = int(a * inv + 0.5)
    return out


def encode_png(width, height, rgba):
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    stride = width * 4
    raw = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride])
                   for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def encode_ico(images):
    """images: list of (size, png_bytes) -> multi-size .ico (PNG entries)."""
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    blob = b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII",
                               size % 256, size % 256, 0, 0, 1, 32,
                               len(data), offset)
        blob += data
        offset += len(data)
    return out + entries + blob


def main():
    os.makedirs(ASSETS, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        rgba = render(s)
        png = encode_png(s, s, rgba)
        images.append((s, png))
        if s == 64:
            with open(os.path.join(ASSETS, "icon.png"), "wb") as fh:
                fh.write(png)
    with open(os.path.join(ASSETS, "icon.ico"), "wb") as fh:
        fh.write(encode_ico(images))
    print("written:", sorted(os.listdir(ASSETS)))


if __name__ == "__main__":
    main()
