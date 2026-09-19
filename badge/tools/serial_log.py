"""Dump the badge's USB serial log to a file for N seconds (no reset)."""
import sys, time, serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 25
out = sys.argv[3] if len(sys.argv) > 3 else "badge_log.txt"

s = serial.Serial(port, 115200, timeout=1)
t0 = time.time()
with open(out, "w") as f:
    while time.time() - t0 < secs:
        line = s.readline().decode("utf-8", "replace").rstrip()
        if line:
            f.write(line + "\n")
            f.flush()
s.close()
