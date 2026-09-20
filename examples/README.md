# Examples

The code the three recorded runs were made on. Copy a folder anywhere outside this repo, start `parity` there, and:

| Folder | Mode | Commands | Our result |
|---|---|---|---|
| `jellyfish/` | `/mode python` | `/check jellyfish`, `/migrate jellyfish` | 12 of 12 kept, 1200 of 1200 hidden tests |
| `redis/` | `/mode c` | `/check redis`, `/migrate redis` | 8 of 8 kept, 800 of 800 hidden tests |
| `deno-text/` | `/mode arkts` | `/check deno-text`, `/migrate deno-text` | checked by hand-written ArkTS on the emulator: 6 of 6, 240 of 240 |

Where the code comes from; each keeps its own licence:

- `jellyfish/jellyfish.py`: the pure-Python implementation from [jellyfish](https://github.com/jamesturk/jellyfish); its licence is in that repository.
- `redis/`: `crc16.c`, `crc64.c`, `endianconv.c`, `lookup3.c` from the [Redis](https://github.com/redis/redis) source, with the notices in each file; `redis.h` is a small stand-in header so they compile alone.
- `deno-text/text.ts`: functions from the Deno standard library, [@std/text](https://github.com/denoland/std/tree/main/text) (MIT), put into one file.

`video/`: `record.py` records the screen during a run (keeps the display awake, restarts if Windows refuses capture), `timelapse.py` speeds one
recording up, `make_demo.py` cuts several recordings into one video. They expect a `recordings/` folder next to them and need ffmpeg.
