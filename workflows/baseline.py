"""Single-agent baseline (the comparison arm). Same gate, integrator, cases, model, attempts and token cap as migrate.py.

  python -m ratchet.framework.run workflows/baseline.py --args '{"profile_dir": "tests/flow_fixture", "run_id": "solo-001"}'

ONE persistent agent does every chunk in dependency order and is its own steward: it can probe the
original and compile, exactly like the team's members can between them. What it does not have is
what the team adds: a second specialist to consult, parallel work, and a shared decision ledger.
"""
import asyncio
import json
from pathlib import Path

from swarmflow import agent, log, phase

from ratchet.engine import gate
from ratchet.engine.contracts import ContractLedger
from ratchet.engine.events import EventLog
from ratchet.engine.integrator import Integrator
from ratchet.engine.tools import RunContext
from ratchet.framework.team import TEAM

META = {
    "name": "ratchet-baseline",
    "description": "Single-agent comparison arm: one agent migrates every chunk under the same gate and budget.",
    "phases": [{"title": "Migrate"}],
}

ROOT = Path(__file__).resolve().parents[1]

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {"files": {"type": "array", "items": {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
        "notes": {"type": "string"}},
    "required": ["files"],
}


async def run(args):
    args = args or {}
    profile_dir = (ROOT / args.get("profile_dir", "tests/flow_fixture")).resolve()
    cases_path = profile_dir / args.get("cases", "cases.jsonl")
    run_dir = ROOT / "runs" / args.get("run_id", "baseline")
    profile = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))
    chunks = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((profile_dir / "chunks").glob("*.json"))}

    resuming = (run_dir / "events.jsonl").exists()
    if resuming and not args.get("resume"):
        raise ValueError(f"run {run_dir.name} already exists; pass resume or pick another run_id")
    events = EventLog(run_dir, run_dir.name)
    fixture_hash = gate.freeze(profile_dir)["fixture_hash"]
    ledger = ContractLedger(profile_dir, run_dir, chunks, events)
    integ = Integrator(profile_dir, run_dir, cases_path, ledger, events)
    ctx = RunContext(profile_dir, run_dir, cases_path, profile, chunks, ledger, integ, events)
    TEAM.reset(run_dir.name)
    ctx.register_solo()

    order, done = [], set()
    while len(done) < len(chunks):
        ready = sorted(c for c, m in chunks.items() if c not in done and set(m.get("depends_on", [])) <= done)
        if not ready:
            raise ValueError("dependency cycle in chunk manifests")
        order += ready
        done |= set(ready)
    reused = integ.load_existing(chunks, fixture_hash) if resuming else []
    events.emit("run.resumed" if resuming else "run.started", actor="scheduler", profile=profile["profile"],
                payload={"chunks": order, "mode": "single-agent", "profile_dir": str(profile_dir), "reused": reused})
    blocked: dict[str, str] = {}
    phase("Migrate")

    for cid in order:
        m = chunks[cid]
        if cid in integ.accepted:
            continue
        if set(m.get("depends_on", [])) & set(blocked):
            blocked[cid] = "a dependency is blocked"
            events.emit("chunk.blocked", actor="scheduler", chunk_id=cid, payload={"reason": blocked[cid]})
            continue
        events.emit("chunk.ready", actor="scheduler", chunk_id=cid)
        deps = integ.files_of(m.get("depends_on", []))
        prompt = (
            f"Chunk {cid}: migrate export(s) {m['exports']} to the target language, preserving behavior exactly.\n"
            f"Write exactly these files and no others: {m['write_allowlist']}.\n{m.get('worker_notes', '')}\n"
            "No `unsafe`, no todo!/unimplemented!.\n\n"
            f"Contracts:\n{ledger.prompt_text(m.get('contract_ids', []))}\n\n"
            f"probe_source inputs are shaped like: {json.dumps(m.get('example_input', {}))}\n\n"
            f"Source:\n{ctx.source_text(cid)}\n"
            + ("Accepted dependencies you may call:" + "".join(f"\n# {p}\n{c}" for p, c in deps.items()) if deps else ""))
        accepted = False
        for attempt in range(1, m.get("limits", {}).get("attempts", 2) + 1):
            label = f"solo-{cid}-attempt{attempt}"
            events.emit("worker.started", actor="scheduler", chunk_id=cid, payload={"label": label, "member": "solo"})
            candidate = await agent(prompt, schema=CANDIDATE_SCHEMA, label=label, options={"member": "solo"})
            if candidate is None:
                log(f"{cid}: agent handed in nothing")
                continue
            candidate["contract_hashes"] = ledger.hashes(m.get("contract_ids", []))
            v = await asyncio.to_thread(gate.check, profile_dir, cid, candidate, cases_path, run_dir, attempt_id=f"{cid}:{label}",
                                        events=events, overlay=deps, current_contract_hashes=ledger.hashes())
            log(f"{cid} {label}: {v.status} ({v.detail[:160]})")
            if v.accepted:
                v = await asyncio.to_thread(integ.integrate, cid, candidate)
                log(f"{cid} integrate: {v.status}")
                accepted = v.accepted
                if accepted:
                    break
            prompt = (f"Chunk {cid}: your candidate was REJECTED: {v.status} at {v.stage}. {v.detail[:600]}\n"
                      f"Counterexample: {json.dumps(v.counterexample)}\nFix it and submit again.")
        if not accepted:
            blocked[cid] = "attempts exhausted"
            events.emit("chunk.blocked", actor="scheduler", chunk_id=cid, payload={"reason": blocked[cid]})

    result = {"profile": profile["profile"], "mode": "single-agent", "accepted": list(integ.accepted), "blocked": blocked,
              "exportable": integ.exportable and not blocked, "accepted_tree": integ.tree_hash()}
    events.emit("run.finished", actor="scheduler", payload=result)
    return result
