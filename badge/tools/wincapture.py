"""Grab the screen already shrunk to badge size (Win32 StretchBlt via ctypes).

Copying every pixel of the monitor into Python (2880x1800 = 5 M pixels) and
shrinking it there took ~67 ms a frame. Here GDI shrinks during the copy, so only a
small image crosses into Python: ~35 ms a frame, measured.

GDI's own smooth mode (HALFTONE) is slow (~50 ms), so we take its fast mode, which
just drops pixels, at OVERSAMPLE x the target size and average that down ourselves.
Same cost as the fast mode alone, and the picture is as smooth as a proper resize.
"""
import ctypes
from ctypes import wintypes

from PIL import Image

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # real pixels, not scaled ones
except OSError:
    pass

SRCCOPY, COLORONCOLOR, DIB_RGB_COLORS = 0x00CC0020, 3, 0
OVERSAMPLE = 4
SM_CXSCREEN, SM_CYSCREEN = 0, 1

# 64-bit handles: without these ctypes truncates them to 32-bit ints.
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.StretchBlt.argtypes = [wintypes.HDC] + [ctypes.c_int] * 4 + [wintypes.HDC] + [ctypes.c_int] * 4 + [wintypes.DWORD]
gdi32.GdiFlush.restype = wintypes.BOOL


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class Capture:
    """Capture screen rectangle (left, top, width, height) shrunk to out_w x out_h."""

    def __init__(self, left, top, width, height, out_w, out_h):
        self.src = (left, top, width, height)
        self.k = max(1, min(OVERSAMPLE, width // out_w, height // out_h))
        out_w, out_h = out_w * self.k, out_h * self.k
        self.out = (out_w, out_h)
        self.screen = user32.GetDC(None)
        self.mem = gdi32.CreateCompatibleDC(self.screen)
        hdr = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), out_w, -out_h, 1, 32, 0)  # top-down BGRX
        bits = ctypes.c_void_p()
        self.bmp = gdi32.CreateDIBSection(self.mem, ctypes.byref(hdr), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        gdi32.SelectObject(self.mem, self.bmp)
        gdi32.SetStretchBltMode(self.mem, COLORONCOLOR)
        self.buf = (ctypes.c_ubyte * (out_w * out_h * 4)).from_address(bits.value)

    def grab(self):
        """Return the current screen as an out_w x out_h RGB PIL image."""
        l, t, w, h = self.src
        ow, oh = self.out
        gdi32.StretchBlt(self.mem, 0, 0, ow, oh, self.screen, l, t, w, h, SRCCOPY)
        gdi32.GdiFlush()
        img = Image.frombuffer("RGB", self.out, self.buf, "raw", "BGRX", 0, 1)
        return img.reduce(self.k) if self.k > 1 else img.copy()


def primary_monitor():
    """Primary monitor rectangle in real pixels."""
    return {"left": 0, "top": 0,
            "width": user32.GetSystemMetrics(SM_CXSCREEN), "height": user32.GetSystemMetrics(SM_CYSCREEN)}
