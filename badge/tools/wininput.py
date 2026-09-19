"""Inject keyboard/mouse into the focused window via Win32 SendInput.

Uses hardware scan codes (KEYEVENTF_SCANCODE) and relative mouse moves, which is
what games like Minecraft (LWJGL/GLFW) actually read. No third-party deps.
"""
import ctypes
from ctypes import wintypes

_send = ctypes.windll.user32.SendInput
PUL = ctypes.POINTER(ctypes.c_ulong)

class _KB(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", PUL)]
class _MS(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", PUL)]
class _U(ctypes.Union):
    _fields_ = [("ki", _KB), ("mi", _MS)]
class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]

_KEYBOARD, _MOUSE = 1, 0
_SCANCODE, _KEYUP = 0x0008, 0x0002
_MOVE = 0x0001

# USB HID / PS2 scan codes
SC = {"W": 0x11, "A": 0x1E, "S": 0x1F, "D": 0x20, "SPACE": 0x39, "LSHIFT": 0x2A,
      "E": 0x12, "Q": 0x10}


def key(scan, down):
    flags = _SCANCODE | (0 if down else _KEYUP)
    inp = _INPUT(type=_KEYBOARD, u=_U(ki=_KB(0, scan, flags, 0, None)))
    _send(1, ctypes.byref(inp), ctypes.sizeof(inp))


def mouse_move(dx, dy):
    inp = _INPUT(type=_MOUSE, u=_U(mi=_MS(int(dx), int(dy), 0, _MOVE, 0, None)))
    _send(1, ctypes.byref(inp), ctypes.sizeof(inp))


def mouse_button(left, down):
    # left: True=left button, False=right; down: press/release
    flags = ({True: 0x0002, False: 0x0008}[left] if down else {True: 0x0004, False: 0x0010}[left])
    inp = _INPUT(type=_MOUSE, u=_U(mi=_MS(0, 0, 0, flags, 0, None)))
    _send(1, ctypes.byref(inp), ctypes.sizeof(inp))
