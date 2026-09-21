"""Parity migration workflow (SwarmFlow). Generic over profiles; everything route-specific is in the fixture.

  python -m parity.framework.run workflows/migrate.py --args '{"profile_dir": "tests/flow_fixture", "run_id": "flow-001"}'

Team: one worker per chunk and one contract steward. All are openJiuwen ReActAgents with memory
and narrow tools (parity/engine/tools.py): workers can compile and can ask the steward; the
steward can run the original implementation. SwarmFlow schedules them; the gate decides.

Per dependency level: independent chunks run in parallel (PARITY_WORKERS at a time). Each chunk gets
at most 2 worker attempts. Steward rulings (asked for by a worker, or triggered by a verifier
counterexample) become versioned decisions that every affected chunk must be re-checked against.
Integration is serial; a candidate written under an older contract version is STALE there
and is sent back to its worker with the new guidance.
"""
import asyncio
import json
import os
import re
from pathlib import Path

from swarmflow import agent, log, parallel, phase

from parity.engine import gate
from parity.engine.contracts import ContractLedger
from parity.engine.events import EventLog
from parity.engine.integrator import Integrator
from parity.engine.tools import DECISION_SCHEMA, RunContext
from parity.framework.team import TEAM

META = {
    "name": "parity-migrate",
    "description": "Gated multi-agent migration of one profile: parallel workers, contract steward, serial integrator.",
    "phases": [{"title": "Plan"}, {"title": "Migrate"}, {"title": "Integrate"}],
}

ROOT = Path(__file__).resolve().parents[1]
MAX_WORKERS = max(1, min(16, int(os.environ.get("PARITY_WORKERS") or 8)))
MAX_STALE_ROUNDS = 2
MAX_TESTER_ROUNDS = 2

REPORT_SCHEMA = {"type": "object", "properties": {"chunk_id": {"type": "string"}, "summary": {"type": "string"}}, "required": ["summary"]}

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

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "chunks": {"type": "array", "items": {"type": "object", "properties": {
            "chunk_id": {"type": "string"}, "depends_on": {"type": "array", "items": {"type": "string"}}, "why": {"type": "string"}},
            "required": ["chunk_id", "depends_on"]}},
        "risks": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["chunks"],
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


def _defines(source: str, name: str) -> bool:
    return re.search(rf"(?m)^[A-Za-z_][\w *]*\b{re.escape(name)}\s*\(", source) is not None


async def run(args):
    args = args or {}
    profile_dir = (ROOT / args.get("profile_dir", "tests/flow_fixture")).resolve()
    cases_path = profile_dir / args.get("cases", "cases.jsonl")
    from parity.paths import RUNS
    run_dir = RUNS / args.get("run_id", "migrate")
    profile = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))
    chunks = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((profile_dir / "chunks").glob("*.json"))}

    resuming = (run_dir / "events.jsonl").exists()
    if resuming and not args.get("resume"):
        raise ValueError(f"run {run_dir.name} already exists; pass resume or pick another run_id")
    # The run works on its own copy of the cases, so inputs the tester finds can be added for good.
    run_dir.mkdir(parents=True, exist_ok=True)
    if not (run_dir / "cases.jsonl").exists():
        (run_dir / "cases.jsonl").write_bytes(cases_path.read_bytes())
    cases_path = run_dir / "cases.jsonl"
    events = EventLog(run_dir, run_dir.name)
    fixture_hash = gate.freeze(profile_dir)["fixture_hash"]
    ledger = ContractLedger(profile_dir, run_dir, chunks, events)
    integ = Integrator(profile_dir, run_dir, cases_path, ledger, events)
    levels = _levels(chunks)
    reused = integ.load_existing(chunks, fixture_hash) if resuming else []
    events.emit("run.resumed" if resuming else "run.started", actor="scheduler", profile=profile["profile"],
                payload={"chunks": list(chunks), "levels": levels, "profile_dir": str(profile_dir), "reused": reused})
    attempts = {c: 0 for c in chunks}
    stale_seq = {c: 0 for c in chunks}
    hunted: set = set()
    ctx = RunContext(profile_dir, run_dir, cases_path, profile, chunks, ledger, integ, events)
    blocked = ctx.blocked
    TEAM.reset(run_dir.name)
    ctx.register_team(steward_model=args.get("steward_model") or os.environ.get("REVIEWER_MODEL"))
    use_tester = args.get("tester", True)
    if use_tester:   # a different model family from the workers on purpose: the attacker should not share the author's blind spots
        ctx.register_tester(model=os.environ.get("TESTER_MODEL") or os.environ.get("REVIEWER_MODEL"))

    async def hunt(cid: str, candidate: dict, overlay: dict, label: str) -> list:
        """The tester attacks a candidate that passed the fixed cases. Inputs it finds become permanent cases."""
        ctx.under_test[cid], ctx.tester_calls[cid], ctx.found[cid] = (candidate, overlay), 0, []
        events.emit("tester.started", actor="scheduler", chunk_id=cid, payload={"label": label})
        report = await agent(ctx.tester_query(cid, candidate), schema=REPORT_SCHEMA, label=f"tester-{label}", options={"member": f"tester-{cid}"})
        ctx.under_test.pop(cid, None)
        found = ctx.found.pop(cid, [])
        events.emit("tester.finished", actor="tester", chunk_id=cid,
                    payload={"found": len(found), "case_ids": [c["case_id"] for c in found], "summary": ((report or {}).get("summary") or "")[:400]})
        return found

    # ── Plan: an agent proposes the dependency order and the semantic risks; plain code checks the plan; the
    # steward settles the risks BEFORE any worker starts, so workers begin with guidance instead of going stale later.
    # Starting from a translation someone already wrote: it is checked first, and agents are only called for what fails.
    start_from = Path(args["start_from"]).resolve() if args.get("start_from") else None
    # Stronger models, in order. A function only moves up to the next one after it ran out of tries on the one before.
    escalate = [x.strip() for x in (args.get("escalate") or os.environ.get("ESCALATE_MODELS") or "").split(",") if x.strip() and x.strip().lower() != "none"]

    def given(cid: str) -> dict | None:
        paths = chunks[cid]["write_allowlist"]
        if start_from is None or not all((start_from / p).exists() for p in paths):
            return None
        return {"files": [{"path": p, "content": (start_from / p).read_text(encoding="utf-8")} for p in paths]}

    if args.get("plan", False) and not resuming and start_from is None:
        phase("Plan")
        ctx.register_planner()
        lang = profile.get("languages", {})
        listing = "\n".join(f"- {cid}: exports {m['exports']}" for cid, m in chunks.items())
        plan = await agent(f"Migration from {lang.get('source', 'the source language')} to {lang.get('target', 'the target language')}.\n"
                           f"Chunks:\n{listing}\nRead the sources, then submit the plan.",
                           schema=PLAN_SCHEMA, label="planner", options={"member": "planner"})
        if plan and not ctx.check_plan(plan):
            deps = {c["chunk_id"]: c.get("depends_on") or [] for c in plan["chunks"]}
            levels = _levels({cid: {"depends_on": deps[cid]} for cid in chunks})
            risks = [r for r in plan.get("risks", []) if isinstance(r, dict) and r.get("question")][:2]
            events.emit("plan.accepted", actor="plan-check", payload={"levels": levels, "chunks": plan["chunks"], "risks": risks})
            for i, risk in enumerate(risks):
                cids = [c for c in risk.get("chunk_ids", []) if c in chunks and chunks[c].get("contract_ids")]
                if not cids:
                    continue
                events.emit("planner.question", actor="planner", chunk_id=cids[0],
                            payload={"question": risk["question"], "to": "steward", "before_workers": True})
                proposal = await agent(ctx.steward_query(cids[0], asked_by="the planner (before any worker starts)",
                                                         question=risk["question"]),
                                       schema=DECISION_SCHEMA, label=f"steward-planner-risk-{i}", options={"member": "steward"})
                decision = ctx.apply_proposal(cids[0], proposal, asked_by="planner")
                events.emit("steward.answered", actor="contract-steward", chunk_id=cids[0],
                            payload={"to": "planner", "decision_id": (decision or {}).get("decision_id"),
                                     "answer": ((proposal or {}).get("answer") or "")[:400]})
        else:
            events.emit("plan.fallback", actor="plan-check", payload={"detail": "no valid plan; using the manifests' dependency order"})

    def task_prompt(cid: str) -> str:
        m = chunks[cid]
        source = "\n\n".join(f"# {p}\n{(profile_dir / p).read_text(encoding='utf-8')}" for p in m["source_files"])
        deps = integ.files_of(m.get("depends_on", []))
        dep_text = "".join(f"\n# accepted dependency {p}\n{c}" for p, c in deps.items())
        inline = [e for d in m.get("depends_on", []) if d not in integ.accepted
                  for e in chunks[d]["exports"] if _defines(source, e)]
        return (
            f"You are a migration worker. Migrate export(s) {m['exports']} from the source below to the target language, "
            "preserving behavior exactly.\n"
            f"Write exactly these files and no others: {m['write_allowlist']}.\n"
            f"{m.get('worker_notes', '')}\n"
            + (f"Override the note above for {inline}: they were NOT migrated, so there is nothing to call. "
               "Their source is in the source below; port them as private functions inside your own file "
               "and call those.\n" if inline else "")
            + "You cannot run anything; an independent verifier will build and test your files. "
            "No `unsafe`, no todo!/unimplemented!.\n\n"
            f"Shared contracts you must follow:\n{ledger.prompt_text(m.get('contract_ids', []))}\n\n"
            f"Source:\n{source}\n{('Accepted dependencies you may call:' + dep_text) if deps else ''}"
        )

    async def consult_steward(cid: str, candidate: dict, verdict) -> dict | None:
        if not chunks[cid].get("contract_ids"):
            return None
        events.emit("steward.consulted", actor="scheduler", chunk_id=cid,
                    payload={"why": verdict.status, "case_id": (verdict.counterexample or {}).get("case_id")})
        proposal = await agent(
            ctx.steward_query(cid, asked_by="the verifier (via scheduler)", counterexample=verdict.counterexample,
                              candidate_files=candidate["files"],
                              build_error=verdict.detail if verdict.status == "REJECTED_BUILD" else ""),
            schema=DECISION_SCHEMA, label=f"steward-{cid}-{attempts[cid]}-{stale_seq[cid]}", options={"member": "steward"})
        return ctx.apply_proposal(cid, proposal, asked_by="scheduler")

    async def settle(cid: str, prior: dict | None = None, stale_reason: str | None = None,
                     counterexample: dict | None = None) -> dict | None:
        """Get one candidate through its own gate check. Returns it stamped with the contract hashes it was written under."""
        m = chunks[cid]
        per_model = max_attempts = m.get("limits", {}).get("attempts", 2)
        stronger = list(escalate)    # models this function has not been moved up to yet

        def stale_prompt(files: list, why: str, cx: dict | None = None) -> str:
            return (task_prompt(cid) + f"\n\nYour earlier candidate is STALE: {why}\n"
                    + (f"It matched its own cases but broke once it joined the code already kept. "
                       f"Counterexample (the source is the truth): {json.dumps(cx)}\n" if cx else
                       "Re-read the contracts above. Return the same files if they already comply, or corrected files.\n")
                    + f"Earlier files: {json.dumps(files)}")

        stale_retry = prior is not None
        prompt = stale_prompt(prior["files"], stale_reason, counterexample) if stale_retry else task_prompt(cid)
        stale_rounds = tester_rounds = 0
        tester_retry = False
        handed = given(cid) if prior is None else None
        while True:
            # A stale or tester re-dispatch is not the worker's fault, so it does not use up an attempt (but is capped).
            if handed is not None:
                label = f"given-{cid}"
            elif tester_retry:
                tester_rounds += 1
                if tester_rounds > MAX_TESTER_ROUNDS:
                    break
                stale_seq[cid] += 1
                label = f"worker-{cid}-tester{tester_rounds}"
            elif stale_retry:
                stale_rounds += 1
                if stale_rounds > MAX_STALE_ROUNDS:
                    break
                stale_seq[cid] += 1
                label = f"worker-{cid}-stale{stale_seq[cid]}"
            else:
                if attempts[cid] >= max_attempts:
                    if not stronger or cid in blocked:
                        break
                    # Trying again with the same model gives the same wrong answer. A stronger one gets the function, with what failed.
                    model = stronger.pop(0)
                    TEAM.specs[f"worker-{cid}"].model = model
                    TEAM.fresh(f"worker-{cid}")
                    max_attempts += per_model
                    events.emit("worker.escalated", actor="scheduler", chunk_id=cid, payload={"model": model})
                attempts[cid] += 1
                label = f"worker-{cid}-attempt{attempts[cid]}"
            member = f"worker-{cid}"
            ctx.mark_seen(member, cid)
            if handed is not None:                       # the translation we were given: no agent is called for it
                candidate, handed = handed, None
                for f in candidate["files"]:
                    events.emit("worker.wrote", actor="given", chunk_id=cid, payload=f)
            else:
                events.emit("worker.started", actor="scheduler", chunk_id=cid, payload={"label": label, "member": member})
                candidate = await agent(prompt, schema=CANDIDATE_SCHEMA, label=label, options={"member": member})
            used = ctx.seen[member]  # includes guidance the worker received from the steward mid-turn
            if candidate is None:
                log(f"{cid}: worker returned nothing usable")
                stale_retry = tester_retry = False
                continue
            candidate["contract_hashes"] = used  # stamped by the scheduler, not self-reported by the worker
            overlay = integ.files_of([c for c in integ.accepted if c != cid])
            v = await asyncio.to_thread(gate.check, profile_dir, cid, candidate, cases_path, run_dir,
                                        attempt_id=f"{cid}:{label}", events=events, overlay=overlay,
                                        current_contract_hashes=ledger.hashes())
            log(f"{cid} {label}: {v.status} ({v.detail[:160]})")
            found = []
            key = (cid, v.candidate_hash, gate._sha(json.dumps(integ.files_of(m.get("depends_on", [])), sort_keys=True).encode()))
            if v.accepted and use_tester and key not in hunted:
                hunted.add(key)
                found = await hunt(cid, candidate, overlay, label)
                if found:
                    # the found inputs are in the case file now: the normal check produces the rejection and its counterexample
                    v = await asyncio.to_thread(gate.check, profile_dir, cid, candidate, cases_path, run_dir,
                                                attempt_id=f"{cid}:{label}-after-tester", events=events, overlay=overlay,
                                                current_contract_hashes=ledger.hashes())
            if v.accepted:
                return candidate
            if v.status == "STALE":
                stale_retry, tester_retry, prompt = True, False, stale_prompt(candidate["files"], v.detail)
                continue
            stale_retry = tester_retry = False
            if v.status == "BLOCKED":   # the checker itself cannot run: no model can fix that, so no more tries are spent on it
                blocked[cid] = f"the checker could not run: {v.detail[:120]}"
                break
            if found:   # the tester broke code that passed every fixed case: that is not an attempt the worker wasted
                tester_retry = True
                prompt = (task_prompt(cid) + "\n\nYour candidate matched every fixed case, then the tester found inputs where it "
                          f"does not: {json.dumps([c['input'] for c in found])}\nFirst difference: {json.dumps(v.counterexample)}\n"
                          f"Previous files: {json.dumps(candidate['files'])}\nFix the rule, not these inputs.")
                continue
            more = cid not in blocked and (attempts[cid] < max_attempts or bool(stronger))
            # Behavior mismatches always go to the steward. Build failures go back only when the worker was
            # following steward guidance: the steward can be wrong too, and only the compiler can tell it so.
            if more and (v.status == "REJECTED_BEHAVIOR" or (v.status == "REJECTED_BUILD" and ctx.has_guidance(cid))):
                decision = await consult_steward(cid, candidate, v)
                if decision:
                    log(f"{cid}: decision {decision['decision_id']} -> affects {decision['affected_chunks']}")
            if not more or cid in blocked:   # nothing is sent after the last attempt, so nothing is bought for it either
                break
            hint = str(await agent(f"Function: {m['exports']}\nError:\n{v.detail[:3000]}\n"
                                   f"Failing cases: {len(v.mismatched_case_ids)}; up to five of them (the source output is the truth):\n"
                                   f"{json.dumps(v.counterexamples)[:4000]}\n\nOriginal:\n{ctx.source_text(cid)[:8000]}\n\n"
                                   f"Code:\n{json.dumps(candidate['files'])[:12000]}",
                                   label=f"hint-{cid}-{attempts[cid]}-{stale_seq[cid]}", options={"member": f"expert-hint-{cid}"}) or "")[:1500]
            if hint:
                events.emit("expert.hint", actor="contract-steward", chunk_id=cid, payload={"hint": hint})
            prompt = (task_prompt(cid)  # includes any new guidance
                      + f"\n\nYour previous candidate was REJECTED: {v.status} at {v.stage}. {v.detail[:600]}\n"
                      f"Counterexamples: {json.dumps(v.counterexamples)[:4000]}\nPrevious files: {json.dumps(candidate['files'])}\n"
                      + (f"The expert read the error and says:\n{hint}\n" if hint else "") + "Fix it.")
        blocked.setdefault(cid, "ran out of tries")
        return None

    async def integrate(cid: str, candidate: dict | None) -> None:
        for i in range(MAX_STALE_ROUNDS + 1):
            if candidate is None:
                break
            v = await asyncio.to_thread(integ.integrate, cid, candidate)
            log(f"{cid} integrate: {v.status} ({v.detail[:160]})")
            if v.accepted:
                blocked.pop(cid, None)
                return
            if i == MAX_STALE_ROUNDS:
                blocked[cid] = f"{v.status}: {v.detail[:300]}"
                break
            # A rejection here is about the tree, not the fixed cases: the worker gets the counterexample and one more go.
            candidate = await settle(cid, prior=candidate, stale_reason=v.detail, counterexample=v.counterexample)
        blocked.setdefault(cid, "does not work together with the functions already kept")
        events.emit("chunk.blocked", actor="scheduler", chunk_id=cid, payload={"reason": blocked[cid]})

    sem = asyncio.Semaphore(MAX_WORKERS)

    async def worker(c: str) -> dict | None:
        async with sem:
            return await settle(c)

    def orphaned(cid: str) -> bool:
        # A caller whose callee was not kept can still be migrated when the callee's source is in its own view to port inline.
        gone = [e for d in chunks[cid].get("depends_on", []) if d in blocked for e in chunks[d]["exports"]]
        source = "\n\n".join((profile_dir / p).read_text(encoding="utf-8") for p in chunks[cid]["source_files"]) if gone else ""
        return any(not _defines(source, e) for e in gone)

    for level in levels:
        orphans = [c for c in level if c not in blocked and orphaned(c)]
        for c in orphans:
            blocked[c] = "a function it calls was not kept"
            events.emit("chunk.blocked", actor="scheduler", chunk_id=c, payload={"reason": blocked[c]})
        level = [c for c in level if c not in integ.accepted and c not in orphans]
        if not level:
            continue
        phase("Migrate")
        for c in level:
            events.emit("chunk.ready", actor="scheduler", chunk_id=c)
        versions_before = ledger.hashes()
        candidates = await parallel([(lambda c=c: worker(c)) for c in level])
        phase("Integrate")
        # A decision made during this level invalidates earlier accepted chunks that relied on the old version.
        changed = [cid for cid, h in ledger.hashes().items() if versions_before.get(cid) != h]
        revalidate = []
        for contract_id in changed:
            revalidate += integ.invalidate(ledger.affected_chunks(contract_id), f"contract {contract_id} changed")
        for c in dict.fromkeys(revalidate):
            prior = {"files": [{"path": p, "content": t} for p, t in integ.files_of([c]).items()]}
            # Cheap path first: accepted code that still passes every check under the new contract version only
            # needs a fresh receipt. The worker is called back only if that re-check fails.
            recheck = {**prior, "contract_hashes": ledger.hashes(chunks[c].get("contract_ids", []))}
            v = await asyncio.to_thread(integ.integrate, c, recheck)
            events.emit("chunk.revalidated", actor="integrator", chunk_id=c, payload={"reason": v.status, "code_changed": False})
            if not v.accepted:
                await integrate(c, await settle(c, prior=prior, stale_reason="a shared contract changed after you were accepted"))
        for c, cand in zip(level, candidates):
            await integrate(c, cand)

    # The hidden test set, once, on what was kept. Before run.finished so the live view shows it; a failure is a result, never fed back.
    if integ.accepted and profile.get("locked_cases"):
        from parity.engine.evaluate import locked_evaluate
        hidden = await asyncio.to_thread(locked_evaluate, run_dir, profile_dir, events=events)
        log(f"hidden test set: {hidden['status']}")

    result = {"profile": profile["profile"], "accepted": list(integ.accepted), "blocked": blocked,
              "stale": sorted(integ.stale), "exportable": integ.exportable and not blocked,
              "decisions": [g["decision_id"] for c in ledger.contracts.values() for g in c["guidance"]],
              "accepted_tree": integ.tree_hash()}
    events.emit("run.finished", actor="scheduler", payload=result)
    return result
