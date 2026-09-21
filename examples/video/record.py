"""Record the whole screen while the demo runs.   python record.py [name]      (press q in this window to stop)

Run it in a SECOND terminal, then start the migration in the first one. Writes recordings/<name>.mkv (name: raw-<time> if none given).
8 frames a second is plenty for a timelapse and keeps the file small.

The screen is kept awake while this runs: when the display sleeps or the PC locks, Windows refuses screen capture and
ffmpeg stops ("Failed to capture image (error 5)"). If that still happens, recording starts again by itself in
<name>-part2.mkv and so on, so the run is never lost. Do not lock the PC (Win+L) during a take.
"""
import ctypes
import subprocess
import sys
import threading
import time
from pathlib import Path

folder = Path(__file__).with_name("recordings")
folder.mkdir(exist_ok=True)
name = sys.argv[1].removesuffix(".mkv") if len(sys.argv) > 1 else f"raw-{time.strftime('%H%M%S')}"
if (folder / f"{name}.mkv").exists():
    name += f"-{time.strftime('%H%M%S')}"                     # never overwrite an earlier take

ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)   # no sleep, no screen-off, until this exits


def take(out: Path) -> bool:
    """One ffmpeg recording. True = it stopped because capture was refused, not because q was pressed."""
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats", "-f", "gdigrab", "-framerate", "8", "-draw_mouse", "0",
                             "-i", "desktop", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)],
                            stderr=subprocess.PIPE)
    seen = bytearray()

    def show():
        while chunk := proc.stderr.read1(4096):
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
            seen.extend(chunk)
            del seen[:-4000]
    reader = threading.Thread(target=show, daemon=True)
    reader.start()
    proc.wait()
    reader.join(2)
    return b"Failed to capture" in seen or b"I/O error" in seen


print(f"recording the screen to recordings/{name}.mkv. press q here to stop.")
part = 1
while take(folder / (f"{name}.mkv" if part == 1 else f"{name}-part{part}.mkv")):
    part += 1
    print(f"\nscreen capture was refused (locked screen, display off or an admin prompt). recording again in part {part}…")
    time.sleep(3)
