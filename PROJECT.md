# Parity — project brief

Hack the North 2026 · Huawei openJiuwen Multi-Agent Challenge (also submitting to Warp, Best Developer Tool)
Team of 4 · 36 hours · Last updated Sept 18, 2026

## One line
Parity is a swarm of AI agents that ports a Python library to Rust one module at a time, where every merge is checked by a referee the agents can't fool.

## The pitch
AI coding agents cheat. Published benchmarks show them editing tests, adding skip markers, and stubbing functions to get a green checkmark. In one benchmark up to half of a leading agent's "successes" depended on tampering with the tests. In another, pass rates jump from 72% to 98% the moment tests are switched off.

**Everyone else shows you green tests. We show you the chunk we rejected.**

## What it does
1. **Scaffold.** Build a Rust crate next to the Python package, wired in through PyO3 so Rust modules can replace Python modules one at a time with the same public API.
2. **Partition.** Parse the import graph, cut it into chunks that don't depend on each other, order them bottom-up. Hub modules go first, alone.
3. **Port in parallel.** Each worker agent ports one chunk in its own git worktree. The existing Python test suite runs against the hybrid at every step.
4. **Escalate, don't guess.** When a worker hits a decision that affects other modules (how a shared type is represented in Rust), it escalates. The coordinator rules once, the ruling goes in a shared **decision ledger**, every other worker reads it before editing.
5. **The gate.** A chunk merges only if all checks pass. Otherwise it goes back with the evidence. Two failures and the module goes on a "needs a human" list with reasons.
6. **Learn.** After a few verified chunks, recurring fixes are distilled into mechanical rules and applied to remaining chunks without calling a model. Cost per file falls during the run.

On screen: Python lines remaining ticking down, a speed benchmark ticking up, test count holding steady.

## Why multiple agents (the honest answer)
Separation of powers. Workers propose. A deterministic gate decides. A ledger keeps them consistent. A different model reviews cold. A single agent grades its own homework. Plus: the repo is ported in parallel, so wall clock drops with worker count.

Most of the system is deliberately NOT an LLM:

| Role | LLM? | Job |
|---|---|---|
| Scaffolder, Partitioner, Integrator | No | Crate setup, chunk map, merges in dependency order. Only the integrator writes to main |
| Coordinator | No (SwarmFlow script) | Spawns workers, owns the ledger, budgets, routes gate results |
| Workers × N | Yes, cheap model | Port one chunk. Forbidden from touching tests |
| Gate / referee | No | See below |
| Rule distiller | Yes, strong model | Turns verified diffs into reusable rules, validated on a held-out chunk |
| Reviewer | Yes, different model, fresh context | Reads the integrated diff cold |

## The gate
A chunk is rejected if any of these fail:
- crate builds, no new warnings-as-errors
- test files are byte-identical to the original
- collected test count did not drop; coverage within 5 points
- no `todo!()`, `unimplemented!()`, or blanket `unsafe`
- Rust does not import or call back into the original Python module
- Python line count for the chunk actually dropped
- benchmark did not regress

## How we prove it beats one Fable agent
We don't assume it does. Same model, same tools, same token budget, same time cap: one agent told "port this to Rust, keep tests green" versus the swarm. **Our gate referees both.** Reported for each: integrity violations, held-out tests (20% of the suite hidden from both), differential fuzzing mismatches (10k random inputs, original vs port), cross-module consistency, wall clock, tokens. We also run targets of different sizes to show where a single agent starts to degrade. If a row goes against us, the row stays.

## Demo (3 minutes)
1. The stat about agents cheating. 2. Repo map on screen, partitioned, workers light up their chunks. 3. An escalation, a ruling in the ledger, other workers revise. 4. **A worker cheats, the gate catches it, CAUGHT on screen with the offending line.** 5. Cost-per-file curve drops as rules kick in. 6. Kill a worker mid-run; its chunk is reassigned. 7. Final: Python lines near zero, tests green, N× faster, short "needs a human" list. 8. The results table versus one agent. 9. The same skill loading in a second host, for portability.

## What we submit
- The tool, packaged as a **Swarm Skill** on Huawei's WorkSwarm, driven by a **SwarmFlow** script
- A real open-source library ported live, as one PR
- The results table
- The mission-control UI and a backup video

## Why this fits the judges
Rubric: collaboration 30, scenario 25, demo 20, technical 15, reusability 10. The judges are likely the openJiuwen platform engineers. Their own papers name portability, self-evolution, explicit budgets and quality gates, an honest "blocked" state, and fault tolerance as priorities or open problems. Parity demonstrates each. They have already seen travel planners, medical consults, research reports, and code-review teams.

## Team lanes
| Lane | Owns |
|---|---|
| Orchestrator | SwarmFlow script, prompts, ledger, Swarm Skill packaging |
| Referee | Gate, cheat detectors, held-out tests, fuzzing, single-agent baseline, results table |
| Target and Rust | Repo choice, PyO3/maturin scaffold, partitioner, benchmark, hand-port one module first as the worked example |
| Mission control and story | Live UI, pitch, video, Devpost |

**Hour one:** agree on four JSON contracts and commit examples: event stream, gate verdict, chunk manifest, ledger entry. Then nobody blocks anybody.
**Checkpoints:** h2 go/no-go · h10 one chunk end to end · h20 full run · h28 feature freeze.
**Cut order if short on time:** rule distiller, then reviewer, then fault injection. Never the gate or the results table.

## Status
- DONE: WorkSwarm 0.2.6 + ast-grep installed and initialized, fully contained in the repo folder. Rust 1.94 on the machine. See `SETUP.md` for commands and six setup traps.
- DONE, as fallback: a Pydantic v1→v2 migration target (iterative/mlem) is set up and measured: 244 tests pass on v1, 0 run on v2, 0 run after the official codemod. Same architecture, less flashy. We fall back to this if the Rust path fails the hour-2 check.
- OPEN: commit to Python→Rust, and pick the target. Needs: pure Python, type-hinted, CPU-bound, well tested, several modules, roughly 3–5k lines. First candidates: sqlparse, mistune. Not yet verified.
- OPEN: an OpenAI-compatible API key in `env/secrets.env`. Ideally two models.

## If you are an AI agent helping on this project
- Use SwarmFlow (deterministic script), not WorkSwarm's autonomous team mode, which has open bugs.
- Never edit, skip, or delete tests. The gate exists to catch exactly that.
- Source `env/activate-swarm.sh` before any `jiuwenswarm-*` command.
- Cross-module decisions go in the ledger, not in your head.
- Emit events in the agreed JSON format so the UI can show them.
- Cache model responses; the network here is unreliable.
