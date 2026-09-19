"""Worker -> gate -> counterexample -> repair -> gate, on the gate self-test fixture.

The worker never sees the test cases or expected values. It only gets a counterexample
after the gate rejects its candidate. At most two attempts, as in the plan.
"""
import asyncio
import json
from pathlib import Path

from swarmflow import agent, log, phase

from ratchet.engine import gate
from ratchet.engine.events import EventLog

META = {
    "name": "ratchet-selftest-loop",
    "description": "One chunk through worker, gate, counterexample-driven repair, and acceptance.",
    "phases": [{"title": "Attempt 1"}, {"title": "Attempt 2"}],
}

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "tests" / "gate_fixture"
CHUNK = "S1"

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
        "question": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["files", "question", "notes"],
}


def _task(chunk: dict) -> str:
    source = "\n\n".join(f"# {p}\n{(PROFILE / p).read_text(encoding='utf-8')}" for p in chunk["source_files"])
    return (
        "Migrate this Python to safe Rust, preserving behavior exactly for all i64 ts and width > 0.\n"
        f"Write exactly these files and no others: {chunk['write_allowlist']}.\n"
        "The frozen harness calls `bucket::bucket(ts: i64, width: i64) -> i64`, so the function must be `pub`.\n"
        "No `unsafe`, no todo!/unimplemented!, no dependencies.\n\n" + source
    )


async def run(args):
    run_dir = ROOT / "runs" / (args or {}).get("run_id", "selftest-loop")
    events = EventLog(run_dir, run_dir.name)
    chunk = json.loads((PROFILE / "chunks" / f"{CHUNK}.json").read_text(encoding="utf-8"))
    gate.freeze(PROFILE)
    events.emit("run.started", actor="scheduler", profile="gate-selftest", payload={"chunks": [CHUNK]})

    prompt, verdict = _task(chunk), None
    for attempt in (1, 2):
        phase(f"Attempt {attempt}")
        attempt_id = f"{CHUNK}:{attempt}"
        events.emit("worker.started", actor="scheduler", chunk_id=CHUNK, attempt_id=attempt_id)
        candidate = await agent(prompt, schema=CANDIDATE_SCHEMA, label=f"worker-{CHUNK}-attempt{attempt}")
        if candidate is None:
            log(f"{attempt_id}: worker returned nothing usable")
            continue
        verdict = await asyncio.to_thread(
            gate.check, PROFILE, CHUNK, candidate, PROFILE / "cases.jsonl", run_dir,
            attempt_id=attempt_id, events=events)
        log(f"{attempt_id}: {verdict.status} at {verdict.stage}: {verdict.detail[:200]}")
        if verdict.accepted:
            break
        prompt = (_task(chunk) + "\n\nYour previous candidate was REJECTED by the verifier.\n"
                  f"Reason: {verdict.status} at stage {verdict.stage}. {verdict.detail[:600]}\n"
                  f"Counterexample: {json.dumps(verdict.counterexample)}\n"
                  f"Your previous files: {json.dumps(candidate['files'])}\nFix it.")

    status = verdict.status if verdict else "BLOCKED"
    if not (verdict and verdict.accepted):
        events.emit("chunk.blocked", actor="scheduler", chunk_id=CHUNK, payload={"reason": status})
    events.emit("run.finished", actor="scheduler", payload={"accepted": [CHUNK] if verdict and verdict.accepted else []})
    return {"chunk": CHUNK, "status": status, "cases": verdict.cases if verdict else None}
