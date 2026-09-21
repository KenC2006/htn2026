"""Turn a recording into a timelapse.   python timelapse.py <name> [seconds_long=90] [title]

<name> is a file in recordings/ (with or without .mkv). If the take was split (<name>-part2.mkv, ...) the parts are joined.
Speeds the recording up to the length you ask for, darkens the edges, pushes the colours a little towards green and
cyan, and adds a title, the speed, and a clock showing the REAL time that has passed. Writes recordings/<name>-timelapse.mp4.
"""
import subprocess
import sys
from pathlib import Path

folder = Path(__file__).with_name("recordings")
if len(sys.argv) < 2:
    sys.exit("which recording?  " + "  ".join(f.stem for f in sorted(folder.glob("*.mkv")) if "-part" not in f.stem))
name = sys.argv[1].removesuffix(".mkv")
parts = [folder / f"{name}.mkv"] + sorted(folder.glob(f"{name}-part*.mkv"))
if not parts[0].exists():
    sys.exit(f"no recordings/{name}.mkv")
raw, out = parts[0], folder / f"{name}-timelapse.mp4"
if len(parts) > 1:                                            # join the parts first, without re-encoding
    raw = folder / f"{name}-joined.mkv"
    (folder / "_parts.txt").write_text("".join(f"file '{f.name}'\n" for f in parts), encoding="utf-8")
    subprocess.check_call(["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-f", "concat", "-safe", "0", "-i", str(folder / "_parts.txt"), "-c", "copy", str(raw)])
    (folder / "_parts.txt").unlink()
want = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
title = sys.argv[3] if len(sys.argv) > 3 else f"PARITY  |  {name}  |  every function proven"

real = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(raw)], text=True))
speed = max(real / want, 1.0)
font = "C\\:/Windows/Fonts/consolab.ttf"
clock = f"%{{eif\\:t*{speed:.4f}/60\\:d\\:2}}\\:%{{eif\\:mod(t*{speed:.4f}\\,60)\\:d\\:2}}"
steps = [
    f"setpts=PTS/{speed:.4f}", "fps=30", "scale=1920:-2:flags=lanczos",
    "eq=contrast=1.08:saturation=1.15:brightness=-0.02", "colorbalance=gs=0.04:bs=0.05:gh=0.03:bh=0.04", "vignette=PI/5",
    "drawbox=x=0:y=0:w=iw:h=54:color=black@0.72:t=fill", "drawbox=x=0:y=54:w=iw:h=2:color=0x00ff9c@0.9:t=fill",
    f"drawtext=fontfile='{font}':text='{title}':x=24:y=16:fontsize=24:fontcolor=0x00ff9c",
    f"drawtext=fontfile='{font}':text='x{speed:.0f}   real time {clock}':x=w-tw-24:y=16:fontsize=24:fontcolor=white",
]
subprocess.check_call(["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-stats", "-i", str(raw), "-vf", ",".join(steps), "-an",
                       "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
print(f"{out}   {real / 60:.1f} real minutes at x{speed:.0f}")
