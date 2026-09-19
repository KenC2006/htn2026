"""Talk to the Hack the North 2026 badge over its USB serial console.

  python badge.py push ../snitch          upload an app folder, then reload
  python badge.py run "heap" "apps"       run console commands
  python badge.py tail 30                 print serial output for N seconds

Port defaults to COM5; override with BADGE_PORT. Same wire protocol as the
official IDE (badge.hackthenorth.com/ide): bare-CR lines, and put payloads
paced at 128 B / 20 ms because the badge's USB RX ring is only 256 bytes.
"""
import os
import re
import sys
import time

import serial

PORT = os.environ.get("BADGE_PORT", "COM5")
PROMPT = b"badge> "
CHUNK, PAUSE = 64, 0.03
SKIP = {"README.md"}


class Badge:
    def __init__(self, port=PORT):
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = port, 115200, 0.05
        s.dtr = s.rts = False  # don't reset the chip on open
        s.open()
        self.s = s
        self.buf = b""

    def wait_for(self, pattern, timeout):
        end = time.time() + timeout
        while time.time() < end:
            self.buf += self.s.read(4096)
            i = self.buf.find(pattern)
            if i >= 0:
                out, self.buf = self.buf[: i + len(pattern)], self.buf[i + len(pattern):]
                return out.decode("utf-8", "replace")
        raise TimeoutError(f"timeout waiting for {pattern!r}; got {self.buf[-200:]!r}")

    def line(self, text):
        self.s.write(text.encode() + b"\r")

    def sync(self):
        self.buf = b""
        self.s.reset_input_buffer()
        self.line("")
        self.wait_for(PROMPT, 3)

    def cmd(self, text, timeout=8):
        self.buf = b""
        self.line(text)
        return self.wait_for(PROMPT, timeout)

    def put(self, remote, data, tries=3):
        """Upload with retries: the badge sometimes drops bytes when its UI is busy."""
        for attempt in range(tries):
            try:
                self._put(remote, data)
                folder, name = remote.rsplit("/", 1)
                if re.search(rf"\s{len(data)}\s+{re.escape(name)}\s", self.cmd(f"ls {folder}")):
                    return
                print(f"  size mismatch on {name}, retrying")
            except TimeoutError:
                print(f"  put {remote} stalled, retrying ({attempt + 1}/{tries})")
            time.sleep(1)
        raise TimeoutError(f"could not upload {remote}")

    def _put(self, remote, data):
        self.buf = b""
        self.line(f"put {remote} {len(data)}")
        self.wait_for(b"READY", 5)
        for i in range(0, len(data), CHUNK):
            self.s.write(data[i:i + CHUNK])
            self.s.flush()
            time.sleep(PAUSE)
        try:
            self.wait_for(f"OK {len(data)}".encode(), 20)
        except TimeoutError:
            self.unwedge(len(data))
            raise

    def unwedge(self, max_bytes):
        """cmd_put has no read timeout: if bytes were dropped it blocks forever.
        Feed it pad bytes until it prints OK and returns to the prompt."""
        self.buf = b""
        for _ in range(0, max_bytes, CHUNK):
            self.s.write(b"\n" * CHUNK)
            self.s.flush()
            time.sleep(PAUSE)
            self.buf += self.s.read(4096)
            if b"OK " in self.buf or PROMPT in self.buf:
                break
        time.sleep(0.5)
        self.sync()

    def tail(self, secs):
        end = time.time() + secs
        while time.time() < end:
            data = self.s.read(4096)
            if data:
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()


def slim(lua):
    """Drop comment-only lines and indentation before upload. The badge holds the whole
    source in RAM while compiling, and RAM is what it lacks. Lines are blanked rather
    than removed so error line numbers still match the file in the repo."""
    out = []
    for line in lua.split(b"\n"):
        s = line.strip()
        out.append(b"" if s.startswith(b"--") and not s.startswith(b"--[") else s)
    return b"\n".join(out)


def push(folder):
    manifest = open(os.path.join(folder, "manifest.cfg"), encoding="utf-8").read()
    slug = next(l.split("=", 1)[1].strip() for l in manifest.splitlines() if l.startswith("slug="))
    remote_dir = f"/littlefs/apps/{slug}"
    b = Badge()
    b.sync()
    b.cmd("mkdir /littlefs/apps")
    b.cmd(f"mkdir {remote_dir}")
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if name in SKIP or name.endswith((".png", ".md")) or not os.path.isfile(path):
            continue
        data = open(path, "rb").read()
        if not name.endswith(".bin"):
            data = data.replace(b"\r\n", b"\n")
        if name.endswith(".lua"):
            data = slim(data)
        print(f"put {remote_dir}/{name} ({len(data)} B)")
        b.put(f"{remote_dir}/{name}", data)
    b.buf = b""
    b.line("reload")
    print(b.wait_for(PROMPT, 10).strip())


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # the Files app's icon is a private-use glyph
    mode, args = sys.argv[1], sys.argv[2:]
    if mode == "push":
        push(args[0])
    elif mode == "run":
        b = Badge()
        b.sync()
        for c in args:
            print(b.cmd(c).rstrip())
    elif mode == "unwedge":
        Badge().unwedge(int(args[0]) if args else 65536)
        print("badge is back at the prompt")
    elif mode == "tail":
        Badge().tail(float(args[0]) if args else 30)
