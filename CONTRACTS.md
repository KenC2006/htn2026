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
  "verify_seconds": 180
}
```

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

## 3. What the engine writes (D reads these; nobody else writes them)

`runs/<run_id>/events.jsonl`, append-only, one event per line:

```json
{"schema_version": 1, "run_id": "demo-001", "sequence": 42, "timestamp": "2026-09-19T12:00:00Z",
 "type": "candidate.rejected", "profile": "py-rust-batch", "chunk_id": "P2", "attempt_id": "P2:1",
 "actor": "verifier", "payload": {"reason": "REJECTED_BEHAVIOR", "case_id": "negative-boundary-01"}}
```

Event `type` values: `run.started`, `chunk.ready`, `worker.started`, `worker.question`, `decision.recorded`,
`candidate.submitted`, `candidate.rejected`, `candidate.stale`, `chunk.accepted`, `chunk.blocked`, `run.finished`.

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

`runs/<run_id>/receipts/<chunk_id>.json`: see plan section 14, "Gate verdict and receipt". Shape is frozen as written there.

## 4. What a worker returns to the engine (A only)

```json
{"files": [{"path": "target_rust/core/src/window_stats.rs", "content": "..."}],
 "question": "", "notes": ""}
```

Workers have no tools. They return files; the gate builds and runs them.
