# Hack the North 2026

Two projects in this repo.

- **Parity** (Huawei openJiuwen track): a team of AI agents migrates a library to a new language, and a checker proves every function still behaves the same. Python to Rust, C to Rust, TypeScript to ArkTS.
- **BadgeCraft** (`badge/`): our own firmware for the HTN badge. It streams a laptop screen to the badge over Wi-Fi. There are a few badge apps too.

## Parity

### The problem

Code migrations are hard. Companies want them for speed, for safety, or to get onto one stack, but one AI agent can't do it alone since a big library codebase doesn't fit in its context. The AI forgets the delicate code structure and its own decisions halfway through, which is not acceptable because the result has to be right. Everything downstream depends on this code and a rewrite that just looks right isn't enough. You need strict proof that the code functions the same way.

### What Parity does

Point Parity at a file and it tells you which functions it can migrate, and why it can't do the rest. You choose the ones you want, and a team of agents rewrites them. A checker then runs the old and new versions on the same inputs and compares every output. The checker is plain code and it keeps a function only when the two versions produce results that match exactly.

```
/check file      which functions can be migrated, and why not for the rest
/migrate file    build the tests, then let the team rewrite the functions you tick
/export          the new code, plus a report of what was checked
```

### How a run works

1. **Scan.** Before any AI runs, a plain-code scanner reads every function. It keeps the ones whose answer depends only on their inputs: no files, network, clock, randomness or shared state. In C it also turns away anything that writes through a pointer. Every function it turns away comes with the reason.
2. **Build the tests.** Nobody writes them. Parity generates inputs for each function and gets the expected answers by running the original. A function that crashes, or answers the same input two different ways, is dropped here. Each function gets 40 visible tests and 100 hidden ones. The hidden ones are locked away and no agent ever sees them.
3. **Split.** The library is cut into one task per function. Each task holds that function, the helpers it uses, and nothing else. The planner orders the tasks so a function comes after the ones it calls.
4. **Rewrite in isolation.** Each worker gets one task and its own conversation. It never sees the rest of the library, the other workers, or the tests. Its code is built and run in a fresh folder with the original source removed, so it can't peek at the answers or lean on someone else's work.
5. **Escalate.** A function gets three tries, and each failed try comes back with the input that broke it. When the tries run out, the function moves up to a stronger model with everything that failed so far. Cheap models do most of the work and the expensive one only sees the hard cases: one earlier run did nine functions on a small open model and one on Claude, $1.48 total. If the strongest model fails too, the function is marked as needing a human. Nothing is kept that didn't pass.
6. **Put it together.** Functions are added one at a time and rechecked with everything already kept, so the library works as a whole. Then the hidden tests run, once.

### Three migrations, three reasons

We picked one real library for each reason a company migrates.

| Mode               | What we ran                                    | Result                                        |
| ------------------ | ---------------------------------------------- | --------------------------------------------- |
| Python → Rust      | jellyfish, a real string matching library      | 12 of 12 functions, 1200 of 1200 hidden tests |
| C → Rust           | checksums and hashes from the Redis source     | 8 of 8 functions, 800 of 800 hidden tests     |
| TypeScript → ArkTS | telemetry logic, run on the HarmonyOS emulator | 3 of 3 functions, 146 of 146 hidden tests     |

**Python → Rust, for speed.** jellyfish matches names that are spelled differently, the kind of code that runs millions of times when you clean a customer database. It stands for the slow Python loop everyone wants in Rust. The catch is that the two languages disagree in small ways: Python strings count characters and Rust strings count bytes, Python raises exceptions and Rust returns errors. The expert settled the string question once, by running the original, and all twelve workers followed it. 28 minutes, about $4.

**C → Rust, for memory safety.** Redis's checksums and hashes are the kind of old, fast, bit-level C that sits under everything and nobody dares touch. C is already fast. What it lacks is a compiler that stops you from reading past a buffer. The catch is arithmetic: C lets unsigned numbers wrap around silently and Rust panics. The expert ruled on wrapping before any worker started. Parity also turned away 13 of the 21 functions it scanned, the ones that write through pointers.

**TypeScript → ArkTS, to reach HarmonyOS.** This one stands for app logic a team already has and needs on a new platform. ArkTS is Huawei's language for HarmonyOS apps. It looks like TypeScript but is stricter: no `any`, no destructuring, no untyped object literals. The new code is built with DevEco, installed on the HarmonyOS emulator, and every test runs on the device itself. That matters because the device is not Node: its regex engine rejects patterns Node accepts. When the app crashes, the device's crash report goes back to the worker.

### How we used SwarmFlow and openJiuwen

The two do different jobs. openJiuwen gives us the agents. SwarmFlow decides who runs when.

**openJiuwen: the agents.** Every team member is an openJiuwen `ReActAgent`, an agent that thinks, calls a tool, reads the result and repeats until it hands in an answer. Each one stays alive for the whole run with its own conversation, so a worker on its second try remembers what it tried first and why it failed. Agents get no shell and no file access. Each role gets a few tools we wrote, and that's all it can touch: workers can compile and ask the expert, the expert can run the original code, the tester can run both versions on inputs it makes up. We also use openJiuwen's rails, hooks that fire around every tool call, to end an agent's turn the moment it submits and to bill every token to the run's budget.

**SwarmFlow: the workflow.** The whole migration is one SwarmFlow script with three phases: Plan, Migrate, Integrate. Functions that don't depend on each other go to workers through SwarmFlow's `parallel`, and a function that calls another waits for it. SwarmFlow also holds the token budget for the run and stops it cleanly when the budget runs out. What was already kept stays on disk, so a run that is cut off picks up where it stopped. We wrote a small backend that plugs our agents into SwarmFlow's engine: when the script asks for "the expert", that becomes one turn with that same long-lived agent.

No agent ever holds the whole library, which is how we get around the context problem.

- **Planner** reads the code, orders the work by dependency, and flags where the two languages behave differently.
- **Workers** rewrite one function each, in parallel. A worker can compile, and it can ask the expert.
- **Expert** answers by running the original code on inputs it picks, then writes a rule every worker has to follow, for example "count text in characters, not bytes". That's how a decision made once reaches the whole library. If a rule changes, work done under the old one is thrown out and redone.
- **Tester** runs on a different model from the workers. It invents inputs to break each function that passed. Anything it finds becomes a permanent test.

The agents never get to say done. Only the checker does.

### Run it

Add `bin` to PATH, go to the folder your code is in, and type `parity`. Then:

    /mode python                 or /mode c, /mode arkts: what you are migrating
    /check pricing.py            which functions can be migrated, and why not for the rest
    /migrate pricing.py          pick functions, tests get built from the original, the agents migrate it
    /status                      what was kept, and the hidden test score
    /export                      the proven new code lands in ./pricing-rust/ (or -arkts), with a report
    /verify pricing.py their-rust    test a translation someone else wrote (Rust in their-rust/target): about a minute, no AI

Commands take the file you name; there is no "current project". A file in the other language is refused until you switch the mode. C works the same way (`/mode c`, `/check crc64.c`, or a folder of `.c` files): the original is compiled with gcc.

Your original code is never changed. The same words work without the console: `parity check pricing.py`, `parity migrate pricing.py`.

`parity new` also takes a folder or a PyPI module name. It only accepts pure functions: anything that touches files, the clock, the network or shared state is refused with the reason. "Proven" means: the same answers as the original on every input tried, inside the input range the project declares. It is testing, not a mathematical proof, and every receipt says so. Everything it makes goes into `.parity/` in your folder.

A run that is cut off (closed terminal, killed process) keeps what it had: `/migrate` on the same file continues it, and so does `parity run <project> --run-id <id> --resume`. A model call that gets no answer in 150 seconds is asked again (`MODEL_TIMEOUT`). Stronger models to move up to are set in `ESCALATE_MODELS`. More in `ARCHITECTURE.md`; the project folder format is in `CONTRACTS.md`; the code the three runs used is in `examples/`.

Models come from OpenRouter (WorkSwarm 0.2.6). The recorded runs used `anthropic/claude-sonnet-5` for the planner and workers and `anthropic/claude-opus-5` for the expert and tester; `/models` changes them.

### Setup

1. Python 3.11+ and `rustc` on PATH.
2. `python -m venv .venv-swarm`, then `.venv-swarm\Scripts\pip install workswarm==0.2.6`
3. Copy `env/secrets.env.example` to `env/secrets.env` and put in an OpenRouter key. Never commit it.
4. `python -m parity doctor` checks the setup.
5. Tests, no model calls, about a minute: `python -m unittest discover -s tests`

### Where things are

| Path | What |
|---|---|
| `parity/engine/` | the checker (`gate.py`), rules, hidden tests, the agents' tools |
| `parity/framework/` | the bridge to SwarmFlow and openJiuwen |
| `parity/newproject.py`, `scan_python.py`, `scan_c.py` | the scanners and the project builder for Python and C |
| `parity/newarkts.py`, `parity/arkts/` | the same for TypeScript to ArkTS |
| `parity/shell.py`, `view.py`, `cli.py` | console, live view, commands |
| `workflows/migrate.py` | the team's workflow |
| `fixtures/telemetry-workbench/` | prepared projects: `py-rust-batch` (Aidan's), `ts-arkts-core` (Daksh's) |
| `examples/` | the jellyfish, Redis and Deno code the runs used, and the video scripts |

### TypeScript to ArkTS setup

`/mode arkts`, then `/check file.ts` and `/migrate file.ts`, as in the other two modes. One `.ts` file that stands alone
(no imports); exported functions whose inputs and result are numbers, strings, booleans or arrays of them. The original
runs under Node. The new ArkTS is compiled by DevEco, signed, installed on the HarmonyOS emulator and run there, one
app launch per test case, so a function is only kept when the device itself gives the same answers. When the app dies
on the device, the rejection carries the device's crash log. `/verify file.ts <folder>` tests ArkTS anyone wrote.
With no file named, the commands use the prepared project `fixtures/telemetry-workbench/ts-arkts-core`.

Needs DevEco Studio, a signing profile and one running emulator: [env/setup-arkts.md](env/setup-arkts.md); `parity doctor`
says what is missing. The scanner and generator are `parity/newarkts.py` and `parity/arkts/`; the device runners are
the prepared project's. Background: [experiments/arkts-smoke](experiments/arkts-smoke/README.md),
[docs/TS_TO_ARKTS_PLAN.md](docs/TS_TO_ARKTS_PLAN.md).

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
