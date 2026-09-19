"""Push an app and open it: python deploy.py ../snitch "Snitch Badge" [--reboot] [--log]

Walks the launcher with injected button presses until the title bar shows the
app's name, then presses A. Prints any Lua error the badge logs on launch.
--reboot launches from a fresh boot (radio apps need the clean heap).
"""
import re
import sys
import time

from badge import Badge, push


def connect(tries=20):
    """Open the console, riding out reboots (leaving a radio app reboots the badge)."""
    for _ in range(tries):
        try:
            b = Badge()
            b.sync()
            return b
        except Exception:
            time.sleep(1.5)
    sys.exit("badge is not answering on the serial console")


folder, title = sys.argv[1], sys.argv[2]
b = connect()
b.cmd("press Home")
b.s.close()
time.sleep(5)  # a radio app's exit reboots the badge; don't upload into that
connect().s.close()
if "--nopush" not in sys.argv:
    push(folder)

b = connect()
if "--reboot" in sys.argv:
    b.line("reboot")
    b.wait_for(b"console started", 25)
    b.s.close()
    time.sleep(2)
    b = connect()
    b.cmd("press Home")  # boot lands on the ID page, not the launcher
    time.sleep(1)
def focused():
    m = re.search(r'lv_label 6,1 \S+ text="([^"]*)"', b.cmd("uitree"))
    return m.group(1) if m else None


# HOME toggles between the launcher and the ID page; make sure we're on the launcher.
for _ in range(3):
    if focused() is not None:
        break
    b.cmd("press Home")
    time.sleep(1)

# The launcher is a paged 4x4 grid: Left/Right walk one row across all pages and
# stop at the ends (no wrap), Up/Down change row. Sweep each row both ways.
def sweep(key):
    # Empty cells all read "Apps", so only call it the end after several repeats.
    prev, repeats = None, 0
    for _ in range(40):
        cur = focused()
        if cur == title:
            return True
        repeats = repeats + 1 if cur == prev else 0
        if repeats >= 4:
            return False
        prev = cur
        b.cmd(f"press {key}")
    return False


found = False
for _ in range(4):
    if sweep("Left") or sweep("Right"):
        found = True
        break
    b.cmd("press Down")
if not found:
    sys.exit(f"could not find {title!r} in the launcher")
out = b.cmd("press A")
time.sleep(4)
b.buf += b.s.read(16384)
log = out + b.buf.decode("utf-8", "replace")
if "--log" in sys.argv:
    print(log)
errors = [l for l in log.splitlines() if "script_app" in l and " E (" in " " + l]
print("\n".join(errors) if errors else f"launched {title}")
