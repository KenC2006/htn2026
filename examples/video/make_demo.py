"""Cut the three recordings into one demo video of about two minutes.   python make_demo.py

Each piece is (recording, from second, to second, speed). Waiting and awkward moments are left out; long stretches of
agents working are sped up hard, commands and results stay slow enough to read. Writes recordings/parity-demo.mp4.
"""
import subprocess
from pathlib import Path

rec = Path(__file__).with_name("recordings")
tmp = rec / "_pieces"
tmp.mkdir(exist_ok=True)
FONT = "C\\:/Windows/Fonts/consolab.ttf"
GREEN = "0x00ff9c"
ENCODE = ["-an", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]

PY, C, TS = "1-python-to-rust-jellyfish.mkv", "2-c-to-rust-redis.mkv", "3-typescript-to-arkts-prepared-project.mkv"
PLAN = [
    ("card", 3.5, "PARITY", "AI rewrites your code in a new language.  Parity proves it still works the same."),
    ("card", 1.8, "1   Python to Rust", "jellyfish, a real string matching library    12 functions"),
    (PY, 2, 28, 2.6, "Python to Rust  |  pick the functions"),
    (PY, 50, 150, 10, "Python to Rust  |  planner asks, the expert runs the original and writes a rule"),
    (PY, 150, 1723, 130, "Python to Rust  |  workers write, the checker compares, the tester attacks"),
    (PY, 1723, 1768, 8, "Python to Rust  |  hidden tests, never seen by any agent"),
    (PY, 1768, 1798, 4, "Python to Rust  |  12 of 12 kept   1200 of 1200 hidden tests"),
    ("card", 1.8, "2   C to Rust", "checksums and hashes from the redis source    8 functions"),
    (C, 8.5, 15, 1.5, "C to Rust  |  8 of 21 functions can be migrated, the rest say why not"),
    (C, 15, 300, 30, "C to Rust  |  the expert rules on integer overflow and memory alignment"),
    (C, 300, 1137, 110, "C to Rust  |  workers write, the checker compares, the tester attacks"),
    (C, 1137, 1160, 6, "C to Rust  |  hidden tests"),
    (C, 1160, 1183, 4, "C to Rust  |  8 of 8 kept   800 of 800 hidden tests"),
    ("card", 1.8, "3   TypeScript to ArkTS", "built with DevEco, every test case runs on the HarmonyOS emulator"),
    (TS, 5, 28, 2.5, "TypeScript to ArkTS  |  the language of HarmonyOS apps"),
    (TS, 28, 860, 80, "TypeScript to ArkTS  |  each test is an app launch on the emulator"),
    (TS, 860, 940, 12, "TypeScript to ArkTS  |  hidden tests on the device"),
    (TS, 940, 953, 2.5, "TypeScript to ArkTS  |  3 of 3 kept   146 of 146 hidden tests"),
    ("card", 5, "23 functions   3 languages   2146 hidden tests   0 differences", "built on Huawei SwarmFlow and openJiuwen agents"),
]


def ffmpeg(*args):
    subprocess.check_call(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args])


pieces = []
for n, step in enumerate(PLAN):
    out = tmp / f"{n:02d}.mp4"
    pieces.append(out)
    if step[0] == "card":
        _, seconds, big, small = step
        ffmpeg("-f", "lavfi", "-i", f"color=c=0x0b0f0e:s=1920x1200:r=30:d={seconds}", "-vf",
               f"drawtext=fontfile='{FONT}':text='{big}':x=(w-tw)/2:y=h/2-70:fontsize={96 if len(big) < 12 else 44}:fontcolor={GREEN},"
               f"drawtext=fontfile='{FONT}':text='{small}':x=(w-tw)/2:y=h/2+40:fontsize=26:fontcolor=white@0.85,"
               f"fade=in:0:8,fade=out:st={seconds - 0.3}:d=0.3", *ENCODE, str(out))
        continue
    source, start, end, speed, label = step
    clock = f"%{{eif\\:({start}+t*{speed})/60\\:d\\:2}}\\:%{{eif\\:mod({start}+t*{speed}\\,60)\\:d\\:2}}"
    steps = [f"setpts=PTS/{speed}", "fps=30", "scale=1920:1200:flags=lanczos",
             "eq=contrast=1.06:saturation=1.12", "drawbox=x=0:y=0:w=iw:h=54:color=black@0.82:t=fill", f"drawbox=x=0:y=54:w=iw:h=2:color={GREEN}@0.9:t=fill",
             f"drawtext=fontfile='{FONT}':text='{label}':x=24:y=16:fontsize=24:fontcolor={GREEN}",
             f"drawtext=fontfile='{FONT}':text='x{speed:g}   real time {clock}':x=w-tw-24:y=16:fontsize=24:fontcolor=white"]
    ffmpeg("-ss", str(start), "-t", str(end - start), "-i", str(rec / source), "-vf", ",".join(steps), *ENCODE, str(out))
    print(f"{n:02d}  {label[:60]:60}  {(end - start) / speed:5.1f}s")

(tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in pieces), encoding="utf-8")
final = rec / "parity-demo.mp4"
ffmpeg("-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-c", "copy", "-movflags", "+faststart", str(final))
print(final)
