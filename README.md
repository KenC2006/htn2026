# Parity

**Parity is a team of AI agents that rewrites your code in a new language and proves, piece by piece, that it still does exactly what the old code did.**

Hack the North 2026, Huawei openJiuwen multi-agent challenge. Built on Huawei's SwarmFlow engine and openJiuwen agents.

## Run it

    bin\parity                 (PowerShell or cmd;  bin/parity in bash)

Then `/run` to migrate, `/mode` to switch language pair, `/check <folder>` to test a translation someone else wrote, `/help` for the rest.
The same commands without the console: `python -m parity run tests/flow_fixture`, `python -m parity check ...`, `python -m parity --help`.
No activate step is needed: the command switches to the project's Python and loads `env/secrets.env` by itself.

## Use it from your own folder

Add `bin` to your PATH once (PowerShell: `$env:Path += ";C:\path	o\HTNin"`), then work wherever your code is:

    cd C:\my\project
    parity new pricing.py        scan it, pick functions, build the tests
    parity run                   the agent team migrates the project made most recently here
    parity export <run id>       report and patch, and the proven Rust lands in ./pricing-rust/

Everything Parity makes goes into `.parity/` in that folder (projects, runs). Inside the Parity repo it uses `projects/` and `runs/` as before. Plain `parity` opens the console in the same way.

## Migrate your own Python code

    python -m parity new <file.py | folder | module name>      (or /new in the console)

A module that is not installed is downloaded from PyPI into `projects/_downloads/`. What happens, in order:

1. A plain-code scan lists every function as migratable or not, with the reason (files, clock, network, randomness, shared state, `*args`, decorators and generators are refused). Helpers, lookup tables and imports inside the same package travel with the function.
2. You tick the functions you want (`--functions a,b` on the command line, `--list` to only see the scan).
3. Input types and ranges come from type hints, else the model suggests them (`--no-ai` for defaults). Suggestions are never trusted.
4. The original is run on generated inputs, twice. That decides the Rust return type, whether it becomes a `Result` (the original raises in range), and whether the function is deterministic. A function that fails is skipped with the reason.
5. `projects/<name>/` is written: a copy of the original, one piece per function with its own contract, 40 test inputs and 100 hidden inputs each, runners, and a crate-free Rust harness that is confirmed to compile.

Then `python -m parity run projects/<name>`. `projects/` is git-ignored because it holds a copy of the library. Python to Rust only for now.

## Set up a fresh clone

1. Python 3.11+ and `rustc` on PATH.
2. `python -m venv .venv-swarm`, then `.venv-swarm\Scripts\pip install workswarm==0.2.6`
3. `cp env/secrets.env.example env/secrets.env` and fill in the OpenRouter key (get it from Ken by DM; never commit it).
4. `python -m parity doctor` checks all of the above and the remaining budget.
5. `python -m unittest tests.test_gate tests.test_flow tests.test_new tests.test_telemetry` (no model calls, about a minute).

Everything stays inside this folder: WorkSwarm's state goes to `.jiuwenswarm/`, runs to `runs/`.

## How it works

| Who | Can do | Job |
|---|---|---|
| planner | read the source | splits the work, flags where the two languages differ |
| workers (parallel) | compile, ask the expert | rewrite one piece each; never see the tests |
| expert | run the original code | settles a question once, as a rule every worker must follow |
| tester | run inputs through both versions | tries to break every piece that passed; what it finds becomes a permanent test |
| checker (plain code, not AI) | build, run old and new on the same inputs, compare | the only thing that can accept a piece |

After the run, a hidden test set that no agent ever saw is run once. Details: `ARCHITECTURE.md`.

## Where things are

| Path | What |
|---|---|
| `parity/engine/` | checker (`gate.py`), shared rules, one-at-a-time integration, hidden test set, the agents' tools |
| `parity/framework/` | the bridge to SwarmFlow and the openJiuwen agents |
| `parity/shell.py`, `view.py`, `cli.py` | console, live view, commands |
| `workflows/migrate.py` | the team's workflow, run by SwarmFlow |
| `tests/flow_fixture/` | the working example project: copy it to add a language pair |
| `fixtures/telemetry-workbench/py-rust-batch/` | the demo project (Aidan's fixture): three batch functions over sensor records, 24 named cases + 96 generated, 300 hidden. His reference Rust is in `tests/telemetry_known_good/` |
| `CONTRACTS.md` | the folder format teammates build against |
| `PITCH.md` | what to say |
| `env/show-run.py`, `env/compare-runs.py` | print a run's event trace; compare runs side by side |

## Traps we already hit

1. WorkSwarm needs Python 3.11+. Use `.venv-swarm`, not the system Python.
2. On Windows, WorkSwarm prints Chinese and crashes a cp1252 console without `PYTHONUTF8=1` (the `parity` command sets it).
3. Without `JIUWENSWARM_HOME`, importing WorkSwarm writes to `~/.jiuwenswarm`. The variable is the PARENT folder (set by the command).
4. The `swarmflow` module only exists inside a script the engine runs; it cannot be imported from a plain Python shell.
5. Do not write `\n` or `\b` inside bash heredocs that generate code or JSON: they turn into real control characters.

## Models (OpenRouter key, $40 cap, expires 2026-09-26)

| Role | Model | $/M in | $/M out |
|---|---|---|---|
| planner, workers (day to day) | `qwen/qwen3-coder-next` | 0.12 | 0.80 |
| expert, tester (day to day) | `moonshotai/kimi-k2.7-code` | 0.71 | 3.21 |
| planner, workers (demo) | `anthropic/claude-sonnet-5` | 2 | 10 |
| expert, tester (demo) | `anthropic/claude-opus-5` | 5 | 25 |
| strongest | `anthropic/claude-fable-5.1` | 10 | 50 |

Cheap models were enough for the toy examples and failed on real libraries (invalid Rust, 1 of 6 on `humanize`); a Fable one-shot of the same six functions passed 600 of 600 hidden tests.

On the cheap models one run of the example costs about 3 cents. On the demo models a real-library function costs roughly 50 cents to a dollar: two `urllib.parse` runs cost about $13. Change models for a session with `/models`; the defaults are in `env/secrets.env`.
