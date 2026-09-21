# Parity architecture: who is an agent, what Huawei's framework does, what we added

## What runs on openJiuwen / WorkSwarm

| Layer | Huawei component | Used for |
|---|---|---|
| Orchestration | **SwarmFlow engine** (`openjiuwen.agent_teams.workflow.engine.run_workflow`) | Runs `workflows/migrate.py`: `parallel`, `phase`, journal and resume, token budgets |
| Agents | **openJiuwen SDK `ReActAgent`** (`openjiuwen.core.single_agent`) | Every team member: reasoning loop, tool calling, per-member conversation memory |
| Tools | **openJiuwen `@tool` / `LocalFunction`**, registered with `Runner.resource_mgr` | The members' tools below |
| Budget | **`SwarmflowBudgetRail`** | Bills real token usage per model call inside the agent loop; stops agents when the run's cap is hit |

Glue we wrote: `parity/framework/backend.py` (a SwarmFlow `AgentBackend` that maps `agent(..., options={"member": ...})` to a turn with that team member) and `parity/framework/team.py` (member registry).

We do **not** use WorkSwarm's stock `TeamWorkerBackend`. Its workers inherit general file and shell tools, which would let a worker read the tests or the original code. Our members get only the tools listed here.

## The team

| Member | Model | Tools | Cannot |
|---|---|---|---|
| `planner` (one, runs first) | `MODEL_NAME` | `read_source`, `submit_plan` (a deterministic check rejects plans that drop a chunk, drop a declared dependency or contain a cycle, and returns the reasons to the agent) | write code, rule on behavior, override manifest dependencies |
| `worker-<chunk>` (one per chunk, parallel) | `MODEL_NAME`; a function that runs out of tries moves to the next model in `ESCALATE_MODELS` | `ask_steward`, `check_compile`, `submit_candidate` | run tests, see cases or expected outputs, read or write files, touch other chunks |
| `tester` (one, serial, remembers which attacks worked) | `REVIEWER_MODEL` (not the workers' model) | `try_inputs` (runs inputs it invents through BOTH the original and the new code), `submit_report` | write expected outputs (the original supplies them), use inputs outside the declared range (plain code refuses them), pass or fail anything itself: an input it finds becomes a permanent case and the normal gate does the rejecting |
| `steward` (one, serial, remembers every ruling) | `REVIEWER_MODEL` (a different model from the workers on purpose) | `probe_source` (runs the frozen ORIGINAL on inputs it chooses), `submit_ruling` | change expected behavior, issue a pass, see candidates' test results beyond the one counterexample it is sent |

Not agents, on purpose (deterministic code in `parity/engine/`): scheduler, contract service, gate, integrator.

## How they actually collaborate (all observable in `runs/<id>/events.jsonl`)

0. **Planning that adapts the run.** The planner reads the sources, sets the parallel levels, and names up to two language-semantics risks. The steward investigates and rules on those *before any worker starts* (`plan.accepted`, then `decision.recorded`), so workers begin with the guidance instead of finding it halfway through and having their work thrown out as stale. If the plan is unusable the run falls back to the manifest order (`plan.fallback`).
1. **Agent-initiated communication.** A worker that meets a source-vs-target semantic gap calls `ask_steward` *before writing code* (`worker.question`).
2. **Investigation with tools.** The steward probes the original implementation on edge cases (`tool.probe_source`), then rules (`decision.recorded`). The contract service accepts only `implementation_clarification`; a `behavior_change` blocks the chunk for a human.
3. **One agent's finding changes other agents' work.** A ruling bumps the contract version. Every chunk using that contract, and everything depending on them, is affected: candidates still being written under the old version are rejected as `STALE` and sent back; accepted receipts are invalidated until revalidated. Later workers receive the guidance up front.
4. **Verification nobody can talk their way past.** The gate builds with the real compiler and compares old vs new outputs case by case. On a behavioral rejection the scheduler sends the counterexample to the steward, which can turn it into guidance for everyone.
5. **Agents working against each other.** Every candidate that passes the fixed cases is attacked by the tester, which reads the new code and hunts for inputs where it disagrees with the original (`tester.started`, `tool.try_inputs`). What it finds joins the case set for good, so the bar only goes up. It caught a candidate that hardcoded the visible test answers on its first try.
6. **Failure handling.** A rejected function returns to its worker with the input that broke it. After its tries (3 by default) the function moves to a stronger model with the failure history (`worker.escalated`); after the last model it is blocked for a human. A model call with no answer in 150 s is retried. Kept functions are on disk with receipts, so a run that is cut off or hits the token budget resumes from them.
7. **Memory.** Each member keeps its conversation for the whole run: a worker's second attempt remembers its first; the steward answers repeat questions from its earlier rulings (`no_decision`).

## Reuse

`workflows/migrate.py` and the engine are profile-agnostic. A new language pair is a folder: source, scaffold with placeholders, two runner commands, cases, chunk manifests, contracts (`CONTRACTS.md`). No engine change.

Three language pairs use it today: Python to Rust, C to Rust, and TypeScript to ArkTS (built with DevEco and run on the HarmonyOS emulator; added by a teammate as a folder plus two optional profile keys, `preflight` and `compile_target`).

For your own code that folder is generated (`parity/scan_c.py` for C, `parity/newarkts.py` for TypeScript). For Python: `python -m parity new <file | folder | module>` scans the code with plain code (`parity/scan_python.py`), runs the original to learn return types, errors and determinism, and writes the project (`parity/newproject.py`, `parity/templates.py`). The model only suggests input ranges, and every suggestion is tried on the real code before it is used.
