"""Ratchet migration workflow (SwarmFlow). Generic over profiles; everything route-specific is in the fixture.

  python -m ratchet.framework.run workflows/migrate.py --args '{"profile_dir": "tests/flow_fixture", "run_id": "flow-001"}'

Per dependency level: independent chunks run in parallel (max 2 workers). Each chunk gets
at most 2 worker attempts. A behavioral rejection goes to the contract steward, whose
ruling becomes a versioned decision that every affected chunk must be re-checked against.
Integration is serial; a candidate written under an older contract version is STALE there
and is sent back to its worker with the new guidance.
"""
import asyncio
import json
from pathlib import Path

from swarmflow import agent, log, parallel, phase

from ratchet.engine import gate
from ratchet.engine.contracts import ContractLedger, DecisionRejected
from ratchet.engine.events import EventLog
from ratchet.engine.integrator import Integrator

META = {
    "name": "ratchet-migrate",
    "description": "Gated multi-agent migration of one profile: parallel workers, contract steward, serial integrator.",
    "phases": [{"title": "Migrate"}, {"title": "Integrate"}],
}

ROOT = Path(__file__).resolve().parents[1]
MAX_WORKERS = 2
MAX_STALE_ROUNDS = 2

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
        "question": {"type": "string", "description": "A question about the shared contract, or empty"},
        "notes": {"type": "string"},
    },
    "required": ["files", "question", "notes"],
}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_id": {"type": "string"},
        "kind": {"type": "string", "enum": ["implementation_clarification", "behavior_change", "no_decision"]},
        "question": {"type": "string"},
        "ruling": {"type": "string", "description": "General target-language guidance any worker on this contract can apply"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["contract_id", "kind", "question", "ruling", "evidence_refs"],
}


def _levels(chunks: dict) -> list[list[str]]:
    done, levels = set(), []
    while len(done) < len(chunks):
        ready = sorted(c for c, m in chunks.items() if c not in done and set(m.get("depends_on", [])) <= done)
        if not ready:
            raise ValueError(f"dependency cycle or unknown dependency among {sorted(set(chunks) - done)}")
        levels.append(ready)
        done |= set(ready)
    return levels


async def run(args):
    args = args or {}
    profile_dir = (ROOT / args.get("profile_dir", "tests/flow_fixture")).resolve()
    cases_path = profile_dir / args.get("cases", "cases.jsonl")
    run_dir = ROOT / "runs" / args.get("run_id", "migrate")
    profile = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))
    chunks = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((profile_dir / "chunks").glob("*.json"))}

    events = EventLog(run_dir, run_dir.name)
    gate.freeze(profile_dir)
    ledger = ContractLedger(profile_dir, run_dir, chunks, events)
    integ = Integrator(profile_dir, run_dir, cases_path, ledger, events)
    levels = _levels(chunks)
    events.emit("run.started", actor="scheduler", profile=profile["profile"], payload={"chunks": list(chunks), "levels": levels})
    attempts = {c: 0 for c in chunks}
    stale_seq = {c: 0 for c in chunks}
    blocked: dict[str, str] = {}

    def task_prompt(cid: str) -> str:
        m = chunks[cid]
        source = "\n\n".join(f"# {p}\n{(profile_dir / p).read_text(encoding='utf-8')}" for p in m["source_files"])
        deps = integ.files_of(m.get("depends_on", []))
        dep_text = "".join(f"\n# accepted dependency {p}\n{c}" for p, c in deps.items())
        return (
            f"You are a migration worker. Migrate export(s) {m['exports']} from the source below to the target language, "
            "preserving behavior exactly.\n"
            f"Write exactly these files and no others: {m['write_allowlist']}.\n"
            f"{m.get('worker_notes', '')}\n"
            "You cannot run anything; an independent verifier will build and test your files. "
            "No `unsafe`, no todo!/unimplemented!.\n\n"
            f"Shared contracts you must follow:\n{ledger.prompt_text(m.get('contract_ids', []))}\n\n"
            f"Source:\n{source}\n{('Accepted dependencies you may call:' + dep_text) if deps else ''}"
        )

    async def consult_steward(cid: str, candidate: dict, verdict) -> dict | None:
        m = chunks[cid]
        if not m.get("contract_ids"):
            return None
        source = "\n\n".join(f"# {p}\n{(profile_dir / p).read_text(encoding='utf-8')}" for p in m["source_files"])
        proposal = await agent(
            "You are the contract steward. A migration candidate was rejected because its behavior differs from the source.\n"
            "Decide whether this reveals a general source-vs-target language difference that OTHER workers on the same "
            "contract could also get wrong. If so, propose an `implementation_clarification`: one general rule for the "
            "target language (not a patch for this function). You may never change the expected behavior; if the source "
            "behavior itself is ambiguous use `behavior_change`; if this is just a local slip use `no_decision`.\n"
            f"Contracts in play:\n{ledger.prompt_text(m['contract_ids'])}\n\n"
            f"Counterexample (source is the truth): {json.dumps(verdict.counterexample)}\n\n"
            f"Source:\n{source}\n\nRejected candidate:\n{json.dumps(candidate['files'])}\n"
            f"evidence_refs must name the source file and the case id. contract_id must be one of {m['contract_ids']}.",
            schema=DECISION_SCHEMA, label=f"steward-{cid}-{attempts[cid]}-{stale_seq[cid]}")
        if not proposal or proposal.get("kind") == "no_decision":
            return None
        events.emit("worker.question", actor="contract-steward", chunk_id=cid,
                    payload={"question": proposal.get("question"), "case_id": (verdict.counterexample or {}).get("case_id")})
        try:
            return ledger.record_decision(proposal, proposed_by="contract-steward", allowed_contracts=m["contract_ids"])
        except DecisionRejected as e:
            log(f"{cid}: steward proposal rejected by contract service: {e}")
            if proposal.get("kind") == "behavior_change":
                blocked[cid] = f"needs a human: {proposal.get('question')}"
            return None

    async def settle(cid: str, prior: dict | None = None, stale_reason: str | None = None) -> dict | None:
        """Get one candidate through its own gate check. Returns it stamped with the contract hashes it was written under."""
        m = chunks[cid]
        max_attempts = m.get("limits", {}).get("attempts", 2)

        def stale_prompt(files: list, why: str) -> str:
            return (task_prompt(cid) + f"\n\nYour earlier candidate is STALE: {why}\nRe-read the contracts above. Return the "
                    f"same files if they already comply, or corrected files.\nEarlier files: {json.dumps(files)}")

        stale_retry = prior is not None
        prompt = stale_prompt(prior["files"], stale_reason) if stale_retry else task_prompt(cid)
        stale_rounds = 0
        while True:
            # A stale re-dispatch is not the worker's fault, so it does not use up an attempt (but is capped).
            if stale_retry:
                stale_rounds += 1
                if stale_rounds > MAX_STALE_ROUNDS:
                    break
                stale_seq[cid] += 1
                label = f"worker-{cid}-stale{stale_seq[cid]}"
            else:
                if attempts[cid] >= max_attempts:
                    break
                attempts[cid] += 1
                label = f"worker-{cid}-attempt{attempts[cid]}"
            used = ledger.hashes(m.get("contract_ids", []))
            events.emit("worker.started", actor="scheduler", chunk_id=cid, payload={"label": label, "contract_hashes": used})
            candidate = await agent(prompt, schema=CANDIDATE_SCHEMA, label=label)
            if candidate is None:
                log(f"{cid}: worker returned nothing usable")
                stale_retry = False
                continue
            candidate["contract_hashes"] = used  # stamped by the scheduler, not self-reported by the worker
            overlay = integ.files_of(m.get("depends_on", []))
            v = await asyncio.to_thread(gate.check, profile_dir, cid, candidate, cases_path, run_dir,
                                        attempt_id=f"{cid}:{label}", events=events, overlay=overlay,
                                        current_contract_hashes=ledger.hashes())
            log(f"{cid} {label}: {v.status} ({v.detail[:160]})")
            if v.accepted:
                return candidate
            if v.status == "STALE":
                stale_retry, prompt = True, stale_prompt(candidate["files"], v.detail)
                continue
            stale_retry = False
            if v.status == "REJECTED_BEHAVIOR":
                decision = await consult_steward(cid, candidate, v)
                if decision:
                    log(f"{cid}: decision {decision['decision_id']} -> affects {decision['affected_chunks']}")
            prompt = (task_prompt(cid)  # includes any new guidance
                      + f"\n\nYour previous candidate was REJECTED: {v.status} at {v.stage}. {v.detail[:600]}\n"
                      f"Counterexample: {json.dumps(v.counterexample)}\nPrevious files: {json.dumps(candidate['files'])}\nFix it.")
            if cid in blocked:
                break
        blocked.setdefault(cid, "attempts exhausted")
        return None

    async def integrate(cid: str, candidate: dict | None) -> None:
        for _ in range(MAX_STALE_ROUNDS + 1):
            if candidate is None:
                break
            v = await asyncio.to_thread(integ.integrate, cid, candidate)
            log(f"{cid} integrate: {v.status} ({v.detail[:160]})")
            if v.accepted:
                blocked.pop(cid, None)
                return
            if v.status != "STALE":
                blocked[cid] = f"{v.status}: {v.detail[:300]}"
                break
            candidate = await settle(cid, prior=candidate, stale_reason=v.detail)
        blocked.setdefault(cid, "could not integrate")
        events.emit("chunk.blocked", actor="scheduler", chunk_id=cid, payload={"reason": blocked[cid]})

    for level in levels:
        level = [c for c in level if not set(chunks[c].get("depends_on", [])) & set(blocked)]
        for skipped in [c for c in chunks if set(chunks[c].get("depends_on", [])) & set(blocked) and c not in blocked]:
            blocked[skipped] = "a dependency is blocked"
            events.emit("chunk.blocked", actor="scheduler", chunk_id=skipped, payload={"reason": blocked[skipped]})
        for i in range(0, len(level), MAX_WORKERS):
            batch = level[i:i + MAX_WORKERS]
            phase("Migrate")
            for c in batch:
                events.emit("chunk.ready", actor="scheduler", chunk_id=c)
            versions_before = ledger.hashes()
            candidates = await parallel([(lambda c=c: settle(c)) for c in batch])
            phase("Integrate")
            # A decision made during this batch invalidates earlier accepted chunks that relied on the old version.
            changed = [cid for cid, h in ledger.hashes().items() if versions_before.get(cid) != h]
            revalidate = []
            for contract_id in changed:
                revalidate += integ.invalidate(ledger.affected_chunks(contract_id), f"contract {contract_id} changed")
            for c in dict.fromkeys(revalidate):
                prior = {"files": [{"path": p, "content": t} for p, t in integ.files_of([c]).items()]}
                await integrate(c, await settle(c, prior=prior, stale_reason="a shared contract changed after you were accepted"))
            for c, cand in zip(batch, candidates):
                await integrate(c, cand)

    result = {"profile": profile["profile"], "accepted": list(integ.accepted), "blocked": blocked,
              "stale": sorted(integ.stale), "exportable": integ.exportable and not blocked,
              "decisions": [g["decision_id"] for c in ledger.contracts.values() for g in c["guidance"]],
              "accepted_tree": integ.tree_hash()}
    events.emit("run.finished", actor="scheduler", payload=result)
    return result
