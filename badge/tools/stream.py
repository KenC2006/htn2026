"""Stream the laptop screen to the badge over its BadgeCraft hotspot.

  python stream.py                # capture primary monitor, stream to 192.168.4.1:3333
  python stream.py --region X Y W H   # capture a specific window rectangle

Join the badge's Wi-Fi ("BadgeCraft", password "minecraft") first, then run this.
Sends 320x240 RGB565 frames (big-endian for the ST7789), one full frame back to back.
Needs: pip install mss numpy pillow
"""
import argparse
import io
import select
import socket
import struct
import threading
import time

from mss import mss
from PIL import Image

W, H = 320, 240
HOST, PORT = "192.168.4.1", 3333


def _s8(b):
    return b - 256 if b > 127 else b


def input_receiver(sock, args, stop):
    """Read button packets from the badge and drive Minecraft (accelerometer unused).

    D-pad          -> walk (W/S/A/D)
    HOME + D-pad   -> look (move the mouse: HOME+L/R = look sideways, HOME+U/D = up/down)
    START -> jump   A -> attack (hold = break)   B -> place
    Packet: 0xAA, x, y, z, buttons, 0x55.  Button bits: UP0 DOWN1 LEFT2 RIGHT3 A4 B5 START6 HOME7.
    """
    import wininput as win
    keys = {}
    mouse = {"L": False, "R": False}

    def hold(scan, want):
        if want and not keys.get(scan):
            win.key(scan, True); keys[scan] = True
        elif not want and keys.get(scan):
            win.key(scan, False); keys[scan] = False

    def hold_mouse(left, want):
        k = "L" if left else "R"
        if want and not mouse[k]:
            win.mouse_button(left, True); mouse[k] = True
        elif not want and mouse[k]:
            win.mouse_button(left, False); mouse[k] = False

    BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT, BTN_A, BTN_B, BTN_START, BTN_HOME = range(8)
    prev_ab = False
    buf = b""
    try:
        while not stop.is_set():
            # select (not settimeout) so we don't change the socket timeout the
            # main thread's sendall relies on; poll so we can see `stop`.
            r, _, _ = select.select([sock], [], [], 0.5)
            if not r:
                continue
            try:
                data = sock.recv(64)
            except OSError:
                break
            if not data:
                break
            buf += data
            while True:
                i = buf.find(b"\xAA")           # packet: 0xAA, buttons, 0x55
                if i < 0:
                    buf = b""; break
                if len(buf) - i < 3:
                    buf = buf[i:]; break
                if buf[i + 2] != 0x55:
                    buf = buf[i + 1:]; continue  # false sync byte, resync
                btn = buf[i + 1]; buf = buf[i + 3:]
                up    = bool(btn & (1 << BTN_UP))
                down  = bool(btn & (1 << BTN_DOWN))
                left  = bool(btn & (1 << BTN_LEFT))
                right = bool(btn & (1 << BTN_RIGHT))
                home  = bool(btn & (1 << BTN_HOME))
                if home:
                    # look mode: D-pad steers the cursor, no walking
                    hold(win.SC["W"], False); hold(win.SC["S"], False)
                    hold(win.SC["A"], False); hold(win.SC["D"], False)
                    lx = (1 if right else 0) - (1 if left else 0)
                    ly = (1 if down else 0) - (1 if up else 0)
                    if args.invx: lx = -lx
                    if args.invy: ly = -ly
                    if lx or ly:
                        win.mouse_move(lx * args.look, ly * args.look)
                else:
                    hold(win.SC["W"], up); hold(win.SC["S"], down)
                    hold(win.SC["A"], left); hold(win.SC["D"], right)
                hold(win.SC["SPACE"], btn & (1 << BTN_START))   # START = jump
                a = bool(btn & (1 << BTN_A))
                b = bool(btn & (1 << BTN_B))
                ab = a and b
                if ab and not prev_ab:              # A+B together = toggle inventory (tap E)
                    win.key(win.SC["E"], True); win.key(win.SC["E"], False)
                prev_ab = ab
                hold_mouse(True,  a and not ab)     # A alone = attack (hold = break)
                hold_mouse(False, b and not ab)     # B alone = place
    finally:
        for scan in list(keys):
            if keys[scan]:
                win.key(scan, False)
        hold_mouse(True, False)
        hold_mouse(False, False)


def stream_once(s, args, sct, mon, sw, sh, min_dt, deadline):
    """Grab/encode/send frames until the socket drops (raises OSError) or deadline.
    Returns the number of frames sent this connection."""
    sent, frames, bytes_sent, t0 = 0, 0, 0, time.time()
    while deadline is None or time.time() < deadline:
        frame_t = time.time()
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.rgb).resize((sw, sh))
        q = args.quality
        while True:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=q)
            data = buf.getvalue()
            if len(data) <= 60000 or q <= 20:   # keep under the badge's 64 KB buffer
                break
            q -= 10                              # busy frame: back off quality, retry
        s.sendall(struct.pack("<I", len(data)) + data)   # length-prefixed frame
        sent += 1; frames += 1; bytes_sent += len(data)
        if time.time() - t0 >= 1.0:
            print(f"{frames} fps sent (~{bytes_sent // max(frames, 1) // 1024} KB/frame)")
            frames, bytes_sent, t0 = 0, 0, time.time()
        if min_dt:                               # pace the send rate
            slack = min_dt - (time.time() - frame_t)
            if slack > 0:
                time.sleep(slack)
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--seconds", type=float, default=0, help="auto-stop after N seconds (0 = run forever)")
    ap.add_argument("--log", help="write an fps summary line to this file on exit")
    ap.add_argument("--quality", type=int, default=50, help="JPEG quality 1-95 (lower = smaller/faster)")
    ap.add_argument("--scale", type=int, default=1, help="downscale factor: 1=320x240 (sharp), 2=160x120 (badge upscales, faster)")
    ap.add_argument("--fps", type=float, default=0, help="cap send rate to N fps (0 = uncapped); lowers badge CPU/current")
    ap.add_argument("--controls", action="store_true", help="drive Minecraft from badge tilt (WASD)")
    ap.add_argument("--look", type=float, default=22, help="cursor pixels per tick while HOME is held")
    ap.add_argument("--deadzone", type=int, default=6, help="(unused) legacy tilt threshold")
    ap.add_argument("--sens", type=float, default=0.9, help="(unused) legacy tilt sensitivity")
    ap.add_argument("--swap", action="store_true", help="swap the two tilt axes if movement is rotated")
    ap.add_argument("--invx", action="store_true", help="invert left/right")
    ap.add_argument("--invy", action="store_true", help="invert forward/back")
    args = ap.parse_args()
    sw, sh = W // args.scale, H // args.scale
    min_dt = 1.0 / args.fps if args.fps else 0.0

    sct = mss()
    mon = sct.monitors[1]
    if args.region:
        x, y, w, h = args.region
        mon = {"left": x, "top": y, "width": w, "height": h}

    start = time.time()
    deadline = start + args.seconds if args.seconds else None
    total_frames = 0
    try:
        while deadline is None or time.time() < deadline:
            try:
                s = socket.create_connection((args.host, PORT), timeout=8)
            except OSError:
                print(f"waiting for badge at {args.host}:{PORT} ... (retry)")
                time.sleep(1.5)
                continue
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(6)     # a stalled send fails in 6s -> reconnect, never hang
            print("connected - streaming (Ctrl-C to stop)")
            stop = threading.Event()
            rx = None
            if args.controls:
                rx = threading.Thread(target=input_receiver, args=(s, args, stop), daemon=True)
                rx.start()
                print("controls ON - focus Minecraft")
            try:
                total_frames += stream_once(s, args, sct, mon, sw, sh, min_dt, deadline)
            except OSError as e:
                print(f"connection lost ({type(e).__name__}) - reconnecting...")
            finally:
                stop.set()
                try:
                    s.close()
                except OSError:
                    pass
                if rx:
                    rx.join(timeout=1)
    except KeyboardInterrupt:
        pass

    elapsed = max(time.time() - start, 1e-6)
    summary = f"streamed {total_frames} frames in {elapsed:.1f}s = {total_frames / elapsed:.1f} fps avg"
    print(summary)
    if args.log:
        with open(args.log, "w") as f:
            f.write(summary + "\n")


if __name__ == "__main__":
    main()
