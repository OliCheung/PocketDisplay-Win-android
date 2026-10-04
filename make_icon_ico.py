"""Pack the Android-rendered PNGs into assets/icon.ico + assets/icon.png.

Run make_icon_android.ps1 first (it produces assets/icon_src_<size>.png).
"""

import os
import struct
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")
SIZES = [16, 24, 32, 48, 64, 128, 256]


def read_png(path):
    with open(path, "rb") as fh:
        return fh.read()


def png_size(data):
    w, h = struct.unpack(">II", data[16:24])
    return w, h


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


def sample_alpha(data, x, y):
    """Read one pixel's alpha from a non-interlaced 8-bit RGBA PNG."""
    pos = 8
    w = h = 0
    idat = b""
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype != 6:
                return None
        elif typ == b"IDAT":
            idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4
    # only handles filter 0 rows, which is what WPF's encoder emits for these
    for row in range(h):
        off = row * (stride + 1)
        if raw[off] != 0:
            return None
    def px(px_, py):
        off = py * (stride + 1) + 1 + px_ * 4
        return tuple(raw[off:off + 4])
    return px


def main():
    images = []
    for s in SIZES:
        path = os.path.join(ASSETS, "icon_src_{}.png".format(s))
        if not os.path.exists(path):
            raise SystemExit("missing {} - run make_icon_android.ps1 first".format(path))
        data = read_png(path)
        w, h = png_size(data)
        if (w, h) != (s, s):
            raise SystemExit("size mismatch in {}: {}x{}".format(path, w, h))
        images.append((s, data))

    ico = encode_ico(images)
    with open(os.path.join(ASSETS, "icon.ico"), "wb") as fh:
        fh.write(ico)

    # header logo: reuse the 256px render
    with open(os.path.join(ASSETS, "icon.png"), "wb") as fh:
        fh.write(images[-1][1])

    # sanity: opaque center, transparent corner
    px = sample_alpha(images[-1][1], 128, 128)
    corner = sample_alpha(images[-1][1], 3, 3)
    print("icon.ico:", len(ico), "bytes, icon.png:",
          len(images[-1][1]), "bytes")
    print("center px:", px, "corner px:", corner)
    if not px or px[3] < 200:
        print("WARNING: center should be opaque")
    if not corner or corner[3] > 40:
        print("WARNING: corner should be transparent (rounded mask)")


if __name__ == "__main__":
    main()
