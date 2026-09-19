# Parity

**Parity is a team of AI agents that rewrites your code in a new language and proves, piece by piece, that it still does exactly what the old code did.**

Hack the North 2026, Huawei openJiuwen multi-agent challenge. Built on Huawei's SwarmFlow engine and openJiuwen agents.

## Run it

    bin\parity                 (PowerShell or cmd;  bin/parity in bash)

Then `/run` to migrate, `/mode` to switch language pair, `/check <folder>` to test a translation someone else wrote, `/help` for the rest.
The same commands without the console: `python -m parity run tests/flow_fixture`, `python -m parity check ...`, `python -m parity --help`.
No activate step is needed: the command switches to the project's Python and loads `env/secrets.env` by itself.

## Set up a fresh clone

1. Python 3.11+ and `rustc` on PATH.
2. `python -m venv .venv-swarm`, then `.venv-swarm\Scripts\pip install workswarm==0.2.6`
3. `cp env/secrets.env.example env/secrets.env` and fill in the OpenRouter key (get it from Ken by DM; never commit it).
4. `python -m parity doctor` checks all of the above and the remaining budget.
5. `python -m unittest tests.test_gate tests.test_flow` (no model calls, about a minute).

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
| planner, workers | `qwen/qwen3-coder-next` | 0.12 | 0.80 |
| expert, tester | `moonshotai/kimi-k2.7-code` (a different family on purpose; reasoning model, needs max_tokens >= 2000) | 0.71 | 3.21 |
| expensive | `anthropic/claude-sonnet-5` 2 / 10, `anthropic/claude-fable-5.1` 10 / 50 | | |

One run of the example costs about 3 cents. Change models for a session with `/models`.
