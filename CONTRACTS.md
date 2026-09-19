# Ratchet hour-one contracts (v1, owner: A)

What B, C and D build against. Change only by telling A; bump `schema_version` if a field changes meaning.
Full background: `Ratchet_Hackathon_Plan.md` section 14.

## 1. What a route owner (B, C) hands to the gate

Each profile (`py-rust-batch`, `c-rust-buffer`, `ts-arkts-core`) provides two commands. Both read a cases file and write an observations file. The gate never trusts anything else.

    <run_source> --cases cases.jsonl --out source_obs.jsonl
    <run_target> --cases cases.jsonl --out target_obs.jsonl --candidate <dir>

Declare them in `fixtures/telemetry-workbench/<profile>/profile.json`:

```json
{
  "schema_version": 1,
  "profile": "py-rust-batch",
  "run_source": ["python", "runners/py_source.py"],
  "run_target": ["python", "runners/py_target.py"],
  "build_target": ["maturin", "develop", "--release"],
  "verify_seconds": 180,
  "frozen": ["profile.json", "chunks/*", "contracts/*", "legacy/*", "harness/*", "runners/*", "cases.jsonl"],
  "oracle_paths": ["legacy", "cases.jsonl"],
  "forbid_patterns": [{"regex": "\\bunsafe\\b", "why": "no application unsafe"}]
}
```

- `frozen`: globs of files no worker may ever change. The gate hashes them before the run and rejects any drift.
- `oracle_paths`: removed from the workspace the candidate is built and run in, so the target cannot call the original.
- `forbid_patterns`: regexes checked against worker files only.
- Commands run with the profile folder (source) or the candidate workspace (build, target) as the working directory. No API keys are in their environment.

**Working example to copy: `tests/flow_fixture/`** (three chunks, one dependency, real `rustc`). Try it:

    . env/activate-swarm.sh
    python -m unittest tests.test_gate tests.test_flow
    python -m ratchet doctor                                   # toolchains, key, budget
    python -m ratchet scan tests/flow_fixture                  # validates YOUR profile folder too, costs nothing
    python -m ratchet run tests/flow_fixture --run-id try-1    # team run (add --solo for the single-agent baseline)
    python -m ratchet status try-1
    python -m ratchet export try-1                             # runs/try-1/export/: migration.patch, report.md, receipts.json
    python env/show-run.py try-1                               # full event trace
    python env/compare-runs.py try-1 other-run                 # side-by-side metrics (for D)

### Case (one JSON object per line in `cases.jsonl`)

```json
{"schema_version": 1, "case_id": "negative-boundary-01", "chunk_id": "P2", "export": "window_stats",
 "input": {"records": [["s1", -1, 0, 5]], "width_ms": 1000}}
```

`case_id` is unique within a file. `input` is whatever the export takes, as plain JSON. Bytes are arrays of ints 0-255.

### Observation (one per case, same `case_id`, in `*_obs.jsonl`)

```json
{"schema_version": 1, "case_id": "negative-boundary-01", "status": "ok",
 "value": [["s1", -1000, 1, 5, 5, 5]], "error_code": null, "duration_ms": 0.4, "diagnostics": ""}
```

- `status`: `ok` | `error` | `crash` | `timeout`. `error` means the API's own declared error; put its frozen code in `error_code` (e.g. `BAD_CHECKSUM`, `ValueError:BAD_WIDTH`) and `value: null`.
- The gate compares `status`, `value`, `error_code` exactly. No sorting, rounding or coercion on your side unless the contract says so. Missing vs zero must stay distinguishable.
- Every case must produce exactly one observation. Missing, duplicate, `crash`, `timeout` all fail the gate.
- `run_target` must not be able to reach the original source implementation.

## 2. Chunk manifest (route owner writes, A schedules from it)

`fixtures/telemetry-workbench/<profile>/chunks/<chunk_id>.json`

```json
{
  "schema_version": 1,
  "chunk_id": "P2",
  "profile": "py-rust-batch",
  "source_files": ["legacy_python/window_stats.py"],
  "exports": ["window_stats"],
  "write_allowlist": ["target_rust/core/src/window_stats.rs"],
  "depends_on": [],
  "contract_ids": ["record-v1", "bucket-semantics"],
  "limits": {"attempts": 2, "verify_seconds_per_attempt": 180}
}
```

Workers may only return files listed in `write_allowlist`. Anything else is `REJECTED_POLICY`.

- `worker_notes` (optional string): what the worker must know about your scaffold, e.g. exact signature, module layout, "must call crate::p1::dedupe_latest". The worker sees the source files, these notes, the contracts, and accepted dependency files. Nothing else.
- `example_input` (object): one hand-written input in the same shape as your case `input`s, e.g. `{"ts": 2500, "width": 1000}`. The steward uses it to know how to call `probe_source`. Do not copy a real test case.
- **Ship a compiling placeholder for every allowlisted file** (see `tests/flow_fixture/target/`). Chunks are built one at a time on top of the accepted tree, so the scaffold must compile before the other chunks exist.
- Tag every case with its `chunk_id`. At integration the gate re-runs the cases of every accepted chunk plus the new one.

### Contracts: `fixtures/telemetry-workbench/<profile>/contracts/<contract_id>.json`

```json
{"schema_version": 1, "contract_id": "bucket-semantics", "version": 1,
 "behavior": "Frozen, observable behavior in plain words. Never edited during a run.", "guidance": []}
```

`guidance` starts empty. The steward's accepted decisions are appended to the run's copy, never to your file.

## 3. What the engine writes (D reads these; nobody else writes them)

`runs/<run_id>/events.jsonl`, append-only, one event per line:

```json
{"schema_version": 1, "run_id": "demo-001", "sequence": 42, "timestamp": "2026-09-19T12:00:00Z",
 "type": "candidate.rejected", "profile": "py-rust-batch", "chunk_id": "P2", "attempt_id": "P2:1",
 "actor": "verifier", "payload": {"reason": "REJECTED_BEHAVIOR", "case_id": "negative-boundary-01"}}
```

Event `type` values: `run.started`, `chunk.ready`, `worker.started`, `worker.question` (a worker asked the steward),
`tool.probe_source` (the steward ran the original), `tool.check_compile`, `steward.consulted` (scheduler sent a counterexample),
`steward.answered`, `decision.recorded`, `decision.rejected`, `run.resumed`, `chunk.resumed` (reused from a still-valid receipt), `evaluation.locked`, `chunk.revalidated` (accepted code re-checked after a contract change),
`candidate.submitted`, `candidate.verified` (passed its own check), `candidate.rejected`, `candidate.stale`,
`chunk.accepted` (integrated into the accepted tree; this is the one to count), `chunk.blocked`, `run.finished`.

Gate verdict `reason` values (in gate order): `STALE`, `REJECTED_POLICY`, `REJECTED_INTEGRITY`, `REJECTED_BUILD`,
`REJECTED_TEST`, `REJECTED_BEHAVIOR`, `REJECTED_INTEGRATION`, `ACCEPTED`.

`runs/<run_id>/decisions.jsonl`:

```json
{"schema_version": 1, "decision_id": "D-003", "contract_id": "bucket-semantics", "version": 2, "supersedes": 1,
 "kind": "implementation_clarification",
 "question": "How should the target compute buckets for negative timestamps?",
 "ruling": "Use div_euclid; Rust `/` truncates toward zero, Python `//` floors.",
 "evidence_refs": ["legacy_python/window_stats.py", "cases/negative-boundary-01"],
 "affected_chunks": ["P2", "P3"], "proposed_by": "contract-steward"}
```

`runs/<run_id>/accepted/`: the accepted files. `runs/<run_id>/verdicts/*.json`: every gate verdict, with the counterexample.

`runs/<run_id>/receipts/<chunk_id>.json`: see plan section 14, "Gate verdict and receipt". Shape is frozen as written there.

## 4. What a worker hands in (A only; via its `submit_candidate` tool)

```json
{"files": [{"path": "target_rust/core/src/window_stats.rs", "content": "..."}],
 "question": "", "notes": ""}
```

Workers have three tools only (ask the steward, compile, submit). The gate builds and runs what they submit. See `ARCHITECTURE.md`.

## 5. Locked evaluation (route owners: please provide one)

Add to `profile.json`: `"locked_cases": "locked/cases.jsonl"`, and put `"locked/*"` in `frozen` and `"locked"` in `oracle_paths`.
Same case format as `cases.jsonl`, generated with **different seeds** from your development cases (see `tests/flow_fixture/gen_cases.py`).
`python -m ratchet evaluate <run_id>` runs the accepted tree against them exactly once, after the run. Results go into each receipt's
`locked_evaluation` and into `report.md`. A failure is kept as a result and makes the run non-exportable; it is never fed back to an agent.
