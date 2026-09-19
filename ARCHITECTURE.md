# Ratchet architecture: who is an agent, what Huawei's framework does, what we added

## What runs on openJiuwen / WorkSwarm

| Layer | Huawei component | Used for |
|---|---|---|
| Orchestration | **SwarmFlow engine** (`openjiuwen.agent_teams.workflow.engine.run_workflow`) | Runs `workflows/migrate.py`: `parallel`, `phase`, journal and resume, token budgets |
| Agents | **openJiuwen SDK `ReActAgent`** (`openjiuwen.core.single_agent`) | Every team member: reasoning loop, tool calling, per-member conversation memory |
| Tools | **openJiuwen `@tool` / `LocalFunction`**, registered with `Runner.resource_mgr` | The members' tools below |
| Budget | **`SwarmflowBudgetRail`** | Bills real token usage per model call inside the agent loop; stops agents when the run's cap is hit |

Glue we wrote: `ratchet/framework/backend.py` (a SwarmFlow `AgentBackend` that maps `agent(..., options={"member": ...})` to a turn with that team member) and `ratchet/framework/team.py` (member registry).

We do **not** use WorkSwarm's stock `TeamWorkerBackend`. Its workers inherit general file and shell tools, which would let a worker read the tests or the original code. Our members get only the tools listed here.

## The team

| Member | Model | Tools | Cannot |
|---|---|---|---|
| `worker-<chunk>` (one per chunk, parallel) | `MODEL_NAME` (Qwen3 Coder) | `ask_steward`, `check_compile`, `submit_candidate` | run tests, see cases or expected outputs, read or write files, touch other chunks |
| `steward` (one, serial, remembers every ruling) | `REVIEWER_MODEL` (Kimi, a different model family on purpose) | `probe_source` (runs the frozen ORIGINAL on inputs it chooses), `submit_ruling` | change expected behavior, issue a pass, see candidates' test results beyond the one counterexample it is sent |

Not agents, on purpose (deterministic code in `ratchet/engine/`): scheduler, contract service, gate, integrator.

## How they actually collaborate (all observable in `runs/<id>/events.jsonl`)

1. **Agent-initiated communication.** A worker that meets a source-vs-target semantic gap calls `ask_steward` *before writing code* (`worker.question`).
2. **Investigation with tools.** The steward probes the original implementation on edge cases (`tool.probe_source`), then rules (`decision.recorded`). The contract service accepts only `implementation_clarification`; a `behavior_change` blocks the chunk for a human.
3. **One agent's finding changes other agents' work.** A ruling bumps the contract version. Every chunk using that contract, and everything depending on them, is affected: in-flight candidates written under the old version are rejected as `STALE` and sent back; accepted receipts are invalidated until revalidated. Later workers receive the guidance up front.
4. **Verification nobody can talk their way past.** The gate builds with the real compiler and compares old vs new outputs case by case. On a behavioral rejection the scheduler sends the counterexample to the steward, which can turn it into guidance for everyone.
5. **Memory.** Each member keeps its conversation for the whole run: a worker's second attempt remembers its first; the steward answers repeat questions from its earlier rulings (`no_decision`).

## Reuse

`workflows/migrate.py` and the engine are profile-agnostic. A new language pair is a folder: source, scaffold with placeholders, two runner commands, cases, chunk manifests, contracts (`CONTRACTS.md`). No engine change.
