"""Screenshot the badge over serial: python shot.py out.png

The console's `shot` streams stripes ("S x0 y0 x1 y1") of base64 "rle1":
a control byte c, then RGB565-LE pixels. c >= 0x80 repeats the next pixel
(c & 0x7f) + 2 times; c < 0x80 is followed by c + 1 literal pixels.
"""
import base64
import struct
import sys
import time
import zlib

from badge import Badge


def grab():
    b = Badge()
    b.sync()
    b.buf = b""
    b.line("shot")
    out, end = b"", time.time() + 30
    while time.time() < end and b"\nEND " not in out:
        out += b.s.read(65536)
    return out.decode("ascii", "replace")


def decode(text):
    lines = text.replace("\r", "").split("\n")
    head = next(l for l in lines if l.startswith("SHOT "))
    w, h = int(head.split()[1]), int(head.split()[2])
    img = bytearray(w * h * 3)
    stripe, payload = None, ""

    def flush():
        if not stripe:
            return
        x0, y0, x1, y1 = stripe
        raw = base64.b64decode(payload)
        x, y = x0, y0

        def emit(px):
            nonlocal x, y
            if y > y1:
                return
            o = (y * w + x) * 3
            img[o:o + 3] = bytes(((px >> 11) * 255 // 31, ((px >> 5) & 63) * 255 // 63, (px & 31) * 255 // 31))
            x += 1
            if x > x1:
                x, y = x0, y + 1

        i = 0
        while i < len(raw):
            c = raw[i]
            i += 1
            if c >= 0x80:  # run of (c & 0x7f) + 2 copies of one pixel
                px = raw[i] | (raw[i + 1] << 8)
                i += 2
                for _ in range((c & 0x7F) + 2):
                    emit(px)
            else:  # c + 1 literal pixels
                for _ in range(c + 1):
                    emit(raw[i] | (raw[i + 1] << 8))
                    i += 2

    for l in lines:
        if l.startswith("S "):
            flush()
            stripe, payload = tuple(int(v) for v in l.split()[1:5]), ""
        elif l.startswith("END"):
            flush()
            break
        elif stripe:
            payload += l
    return w, h, img


def png(path, w, h, rgb):
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    rows = b"".join(b"\0" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    w, h, img = decode(grab())
    png(sys.argv[1], w, h, img)
    print(f"saved {sys.argv[1]} ({w}x{h})")
