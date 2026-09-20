# Hack the North 2026

Two projects in this repo.

- **Parity** (Huawei openJiuwen track): rewrites Python in Rust and proves each function still does what the original did.
- **BadgeCraft** (`badge/`): our own firmware for the HTN badge. It streams a laptop screen to the badge over Wi-Fi. There are a few badge apps too.

## Parity

A model can translate code in seconds. What it can't do is tell you which parts it got wrong. Parity runs the old and new code on the same inputs and only keeps a function when every answer matches. When one fails, a team of agents fixes it.

We tested it on 60 functions from real Python libraries (humanize, statistics, colorsys, html, shlex, idna, inflection, urllib.parse):

| | functions proven on hidden inputs |
|---|---|
| Claude Fable, one attempt, no checking | 59 of 60 |
| Fable + Parity | 60 of 60 |
| fake translation that returns constants | 0 of 60 |

Fable's one miss didn't compile, and its own notes didn't mention it. Parity found it and the agents fixed it.

### Run it

Add `bin` to PATH, go to the folder your Python is in, and type `parity`. Then:

    /mode python                 or /mode c: what you are migrating
    /check pricing.py            which functions can be migrated, and why not for the rest
    /migrate pricing.py          pick functions, tests get built from the original, the agents migrate it
    /status                      what was kept, and the hidden test score
    /export                      the proven Rust lands in ./pricing-rust/
    /verify pricing.py their-rust    test a translation someone else wrote (Rust in their-rust/target): about a minute, no AI

Commands take the file you name; there is no "current project". A file in the other language is refused until you switch the mode. C works the same way (`/mode c`, `/check crc64.c`, or a folder of `.c` files): the original is compiled with gcc.

Your Python is never changed. The same words work without the console: `parity check pricing.py`, `parity migrate pricing.py`.

`parity new` also takes a folder or a PyPI module name. It only accepts pure functions: anything that touches files, the clock, the network or shared state is refused with the reason. "Proven" means: the same answers as the original on every input tried, inside the input range the project declares. It is testing, not a mathematical proof, and every receipt says so. Everything it makes goes into `.parity/` in your folder.

### Who does what

| Who | Job |
|---|---|
| planner | orders the work, flags where Python and Rust differ |
| workers (parallel) | write one function each; never see the tests |
| expert | runs the original to settle a question, writes the answer as a rule for every worker; reads compile errors and tells the worker the fix |
| tester | makes up inputs to break a function that passed; what it finds becomes a permanent test |
| checker (plain code, not AI) | compiles, runs old and new on the same inputs, compares. Nothing else can accept a function |

A function that runs out of tries moves up to a stronger model (`ESCALATE_MODELS`). At the end of a run, hidden inputs no agent saw are run once on what was kept. A run that is cut off (closed terminal, killed process) keeps what it had: `/migrate` on the same file continues it, and so does `parity run <project> --run-id <id> --resume`. A model call that gets no answer in 150 seconds is asked again (`MODEL_TIMEOUT`). More in `ARCHITECTURE.md`; the project folder format is in `CONTRACTS.md`.

Built on Huawei's SwarmFlow engine and openJiuwen agents (WorkSwarm 0.2.6). Models come from OpenRouter.

### Setup

1. Python 3.11+ and `rustc` on PATH.
2. `python -m venv .venv-swarm`, then `.venv-swarm\Scripts\pip install workswarm==0.2.6`
3. Copy `env/secrets.env.example` to `env/secrets.env` and put in an OpenRouter key. Never commit it.
4. `python -m parity doctor` checks the setup.
5. Tests, no model calls, about a minute: `python -m unittest tests.test_gate tests.test_flow tests.test_new tests.test_telemetry`

### Where things are

| Path | What |
|---|---|
| `parity/engine/` | the checker (`gate.py`), rules, hidden tests, the agents' tools |
| `parity/framework/` | the bridge to SwarmFlow and openJiuwen |
| `parity/newproject.py`, `scan_python.py` | `parity new` |
| `parity/shell.py`, `view.py`, `cli.py` | console, live view, commands |
| `workflows/migrate.py` | the team's workflow |
| `fixtures/telemetry-workbench/py-rust-batch/` | the demo project (Aidan's) |
| `env/bench.py` | the benchmark above |

### Models

| Role | Day to day | Demo |
|---|---|---|
| planner, workers | `qwen/qwen3-coder-next` | `anthropic/claude-sonnet-5` |
| expert, tester | `moonshotai/kimi-k2.7-code` | `anthropic/claude-opus-5` |

The cheap models handle the small examples for a few cents a run. On real libraries they mostly fail, which is what `--start-from` and the move to stronger models are for.

### Windows traps

- WorkSwarm prints Chinese and crashes a cp1252 console without `PYTHONUTF8=1`. The `parity` command sets it.
- Without `JIUWENSWARM_HOME`, importing WorkSwarm writes to your home folder. The command sets that too.
- `swarmflow` can only be imported inside a script the engine runs.

## BadgeCraft

The badge is an ESP32-C3 with a 320x240 ST7789 screen. We dumped the stock firmware, worked out the pins from it, and wrote our own.

The badge becomes a Wi-Fi hotspot and a TCP server. The laptop joins, captures its screen, and sends JPEG frames. The badge decodes them with the JPEG decoder already in the chip's ROM and draws them. Raw frames managed about 1 fps; JPEG is what made it usable.

    cd badge/firmware
    idf.py set-target esp32c3 && idf.py build && idf.py -p COM5 flash
    cd ../tools
    python stream.py            (join Wi-Fi "BadgeCraft" first)

Needs ESP-IDF 5.3+ and `pip install mss numpy pillow`. Details and the pin map are in `badge/firmware/README.md`.

Flashing replaces the stock firmware. `tools/restore.py` puts it back from a full flash dump. Take your own with `esptool read_flash` before flashing. Ours are not in the repo because a dump holds the owner's contacts and badge token.

Lua apps for the stock firmware, pushed with `tools/deploy.py`:

| App | What |
|---|---|
| `doomish/` | a raycaster shooter drawn with box widgets, because the badge has no pixel API |
| `snitch/` | a name tag that tells on you |
| `cbradio/` | CB-radio text chat between badges |
| `probe/` | a scratch app for trying things |

## ArkTS route (in development)

The verified DevEco smoke app is in [experiments/arkts-smoke](experiments/arkts-smoke/README.md).
The [TypeScript-to-ArkTS implementation plan](docs/TS_TO_ARKTS_PLAN.md) maps it
onto Parity's existing Python-to-Rust runner and gate architecture. A first
profile (`fixtures/telemetry-workbench/ts-arkts-core`, three hand-ported
functions) runs and verifies on the real HarmonyOS emulator end-to-end; the
agent-driven migration workflow and automatic TypeScript onboarding are not
implemented yet.
