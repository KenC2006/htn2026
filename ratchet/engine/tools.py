"""The only things a team member can do besides think. Each tool is executed by the engine.

Worker tools:   check_compile (build only; reveals nothing about tests), ask_steward (agent-to-agent question)
Steward tools:  probe_source (run the frozen ORIGINAL implementation on inputs the steward chooses)

No tool exposes the test cases, expected outputs, the file system, a shell, or credentials.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..framework.team import TEAM, MemberSpec, ToolSpec
from . import gate
from .contracts import ContractLedger, DecisionRejected
from .events import EventLog
from .integrator import Integrator

WORKER_PROMPT = (
    "You are a migration worker on a small team. You port ONE chunk of code to the target language, preserving behavior "
    "exactly. You cannot run tests; an independent verifier will. Tools:\n"
    "- ask_steward(question): ask the contract steward how a source-language behavior must be reproduced in the target "
    "language. The steward can run the original code; you cannot. ASK BEFORE WRITING CODE whenever the source relies on "
    "language semantics that may differ in the target (integer division, modulo, overflow, sort stability, string "
    "ordering, empty inputs). Guessing wastes one of your two attempts.\n"
    "- check_compile(path, content): build your file with the real compiler. Once it says BUILD_OK, submit; do not "
    "re-check unchanged code.\n"
    "- submit_candidate(path, content, notes): hand in one finished file. Call it once per file you are allowed to write, "
    "then stop. This is the ONLY way to hand in work.\n"
    "Follow every piece of contract guidance you are given. Write only the files you are allowed to write."
)

STEWARD_PROMPT = (
    "You are the contract steward of a code-migration team. Workers ask you how source behavior must be reproduced in "
    "the target language, and the scheduler sends you verifier counterexamples. You own the shared contracts' "
    "implementation guidance: what you rule is sent to every worker whose chunk uses that contract.\n"
    "Rules: (1) The original implementation is the truth. ALWAYS call probe_source on a few inputs, including edge "
    "cases, before ruling. (2) You may only clarify HOW to implement the frozen behavior in the target language "
    "(kind=implementation_clarification). You may never change expected behavior; if the source itself is ambiguous, "
    "use kind=behavior_change and a human will decide. (3) If existing guidance already covers the question, use "
    "kind=no_decision and just answer. (4) Rulings must be general rules for the target language, not patches to one "
    "function. Name a target-language construct only if you are certain of its exact spelling; you cannot compile, "
    "workers can, so otherwise describe the required behavior precisely and let them find the construct. (5) Probe several inputs in ONE probe_source call. "
    "(6) Finish every request by calling submit_ruling exactly once; that is the only way to answer."
)

PLANNER_PROMPT = (
    "You are the planner of a code-migration team. You are given the chunks of a migration (ids and exported names only). "
    "Read the sources with read_source, then hand in a plan with submit_plan. The plan is a JSON object:\n"
    '{"chunks": [{"chunk_id": "...", "depends_on": ["chunk ids whose code this chunk CALLS"], "why": "one sentence"}], '
    '"risks": [{"contract_hint": "short name", "chunk_ids": ["..."], "question": "one precise question about a '
    'source-language behavior that may not carry over to the target language"}]}\n'
    "Rules: a chunk depends on another only if it calls its code; chunks with no dependency between them will be migrated "
    "in parallel, so do not invent dependencies. List at most 2 risks, and only for constructs that literally appear in the "
    "sources you read and whose behavior differs between the two languages, so that a literal translation would be silently "
    "wrong. Go through the operators and library calls in the sources one by one: if any of them gives a different result in the "
    "target language for some input (for example negative numbers, very large numbers, empty input), you MUST list it, "
    "because every worker would otherwise get it wrong separately. One risk per distinct behavior, and nothing about "
    "constructs that are not in the sources. The steward settles each risk BEFORE any worker starts. A deterministic check validates "
    "your plan; if it is rejected, fix it and submit again."
)

SOLO_PROMPT = (
    "You are a single migration agent. You port code chunk by chunk to the target language, preserving behavior exactly. "
    "You cannot run tests; an independent verifier will. Tools:\n"
    "- probe_source(export, inputs_json): run the ORIGINAL implementation on inputs you choose. Use it whenever the source "
    "relies on language semantics that may differ in the target (integer division, modulo, overflow, sort stability, "
    "string ordering, empty inputs). Guessing wastes one of your two attempts per chunk.\n"
    "- check_compile(chunk_id, path, content): build your file with the real compiler. Once it says BUILD_OK, submit.\n"
    "- submit_candidate(chunk_id, path, content, notes): hand in one finished file. The ONLY way to hand in work.\n"
    "Write only the files you are allowed to write."
)

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_id": {"type": "string"},
        "kind": {"type": "string", "enum": ["implementation_clarification", "behavior_change", "no_decision"]},
        "question": {"type": "string"},
        "ruling": {"type": "string", "description": "General target-language rule every worker on this contract must follow"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}, "description": "source file path and the probes or case id you relied on"},
        "answer": {"type": "string", "description": "Short direct answer to whoever asked"},
    },
    "required": ["contract_id", "kind", "question", "ruling", "evidence_refs", "answer"],
}


@dataclass
class RunContext:
    profile_dir: Path
    run_dir: Path
    cases_path: Path
    profile: dict
    chunks: dict
    ledger: ContractLedger
    integ: Integrator
    events: EventLog
    seen: dict = field(default_factory=dict)      # member -> contract hashes that member has actually been shown
    blocked: dict = field(default_factory=dict)
    _n: int = 0

    def source_text(self, cid: str) -> str:
        return "\n\n".join(f"# {p}\n{(self.profile_dir / p).read_text(encoding='utf-8')}" for p in self.chunks[cid]["source_files"])

    def handed_in(self, member: str, cid: str | None = None) -> bool:
        """True once the member has submitted every file of the chunk (any one chunk, when cid is None)."""
        paths = {f["path"] for f in TEAM.outbox.get(member, {}).get("files", [])}
        return any(paths >= set(self.chunks[c]["write_allowlist"]) for c in ([cid] if cid else self.chunks))

    def mark_seen(self, member: str, cid: str) -> None:
        self.seen[member] = self.ledger.hashes(self.chunks[cid].get("contract_ids", []))

    # ── steward ────────────────────────────────────────────────────────────
    def has_guidance(self, cid: str) -> bool:
        return any(self.ledger.contracts[c]["guidance"] for c in self.chunks[cid].get("contract_ids", []))

    def steward_query(self, cid: str, *, asked_by: str, question: str = "", counterexample: dict | None = None,
                      candidate_files: list | None = None, build_error: str = "") -> str:
        m = self.chunks[cid]
        parts = [f"Request from {asked_by} about chunk {cid} (exports {m['exports']}).",
                 f"Contracts in play:\n{self.ledger.prompt_text(m['contract_ids'])}",
                 f"Source:\n{self.source_text(cid)}"]
        if question:
            parts.append(f"Worker's question: {question}")
        if counterexample:
            parts.append("The verifier rejected a candidate. Counterexample (source output is the truth): "
                         f"{json.dumps(counterexample)}\nRejected candidate: {json.dumps(candidate_files)}\n"
                         "Decide whether this shows a general source-vs-target difference other workers could also hit.")
        if build_error:
            parts.append("A candidate that FOLLOWED your guidance did not compile. The real compiler said:\n"
                         f"{build_error[-900:]}\nCandidate: {json.dumps(candidate_files)}\n"
                         "If your guidance named a construct that does not exist in the target language or was otherwise "
                         "wrong, issue a corrected implementation_clarification that replaces it and say which decision it "
                         "corrects. If the worker simply made its own mistake, use no_decision.")
        parts.append(f"probe_source takes export (one of {sorted({e for c in self.chunks.values() for e in c['exports']})}) "
                     f"and inputs_json, a JSON array whose items are shaped like: {self._example_input(cid)}.\n"
                     f"contract_id must be one of {m['contract_ids']}. Finish by calling submit_ruling.")
        return "\n\n".join(parts)

    def _example_input(self, cid: str) -> str:
        # Shape only: one hand-written example per chunk from the manifest, never a real test case.
        return json.dumps(self.chunks[cid].get("example_input", {}))

    def apply_proposal(self, cid: str, proposal: dict | None, *, asked_by: str) -> dict | None:
        """Validate and record a steward proposal. Returns the recorded decision, or None."""
        if not proposal or proposal.get("kind") == "no_decision":
            return None
        try:
            return self.ledger.record_decision(proposal, proposed_by="contract-steward",
                                               allowed_contracts=self.chunks[cid]["contract_ids"])
        except DecisionRejected as e:
            self.events.emit("decision.rejected", actor="contract-service", chunk_id=cid,
                             payload={"why": str(e), "kind": proposal.get("kind"), "asked_by": asked_by})
            if proposal.get("kind") == "behavior_change":
                self.blocked[cid] = f"needs a human: {proposal.get('question')}"
            return None

    # ── tools ──────────────────────────────────────────────────────────────
    def worker_tools(self, member: str, cid: str) -> list[ToolSpec]:
        ctx = self

        async def check_compile(path: str, content: str) -> str:
            ctx._n += 1
            v = await asyncio.to_thread(
                gate.check, ctx.profile_dir, cid, {"files": [{"path": path, "content": content}]}, ctx.cases_path,
                ctx.run_dir, attempt_id=f"{cid}:compile-{ctx._n}", overlay=ctx.integ.files_of(ctx.chunks[cid].get("depends_on", [])),
                stop_after_build=True)
            ctx.events.emit("tool.check_compile", actor=member, chunk_id=cid, payload={"result": v.status})
            return "BUILD_OK" if v.status == "BUILD_OK" else f"{v.status}: {v.detail[-1200:]}"

        async def ask_steward(question: str) -> str:
            ctx.events.emit("worker.question", actor=member, chunk_id=cid, payload={"question": question, "to": "steward"})
            text, _, submitted = await TEAM.ask("steward", ctx.steward_query(cid, asked_by=member, question=question))
            from ..framework.backend import _parse_json
            proposal = submitted or _parse_json(text)
            decision = ctx.apply_proposal(cid, proposal, asked_by=member)
            ctx.events.emit("steward.answered", actor="contract-steward", chunk_id=cid,
                            payload={"to": member, "decision_id": (decision or {}).get("decision_id"),
                                     "answer": ((proposal or {}).get("answer") or text)[:400]})
            ctx.mark_seen(member, cid)  # the reply below shows the worker the current guidance
            return (f"Steward: {(proposal or {}).get('answer') or text}\n\nCurrent contract guidance (binding):\n"
                    f"{ctx.ledger.prompt_text(ctx.chunks[cid]['contract_ids'])}")

        async def submit_candidate(path: str, content: str, notes: str = "") -> str:
            box = TEAM.outbox.setdefault(member, {"files": [], "question": "", "notes": ""})
            box["files"] = [f for f in box["files"] if f["path"] != path] + [{"path": path, "content": content}]
            box["notes"] = notes or box["notes"]
            return f"Received {path}. Submit any other file you are allowed to write, otherwise stop now; the verifier takes it from here."

        return [ToolSpec("submit_candidate", "Hand in ONE finished file for verification. This is the only way to hand in work.", submit_candidate),
                ToolSpec("check_compile", "Build ONE file of yours with the real target compiler. Returns BUILD_OK or the compiler errors. Runs no tests.", check_compile),
                ToolSpec("ask_steward", "Ask the contract steward how a source behavior must be reproduced in the target language. Returns a binding answer.", ask_steward)]

    def steward_tools(self, actor: str = "contract-steward") -> list[ToolSpec]:
        ctx = self

        async def probe_source(export: str, inputs_json: str) -> str:
            try:
                inputs = json.loads(inputs_json)
            except json.JSONDecodeError as e:
                return f"inputs_json is not valid JSON: {e}"
            inputs = (inputs if isinstance(inputs, list) else [inputs])[:8]
            cases = [{"schema_version": 1, "case_id": f"probe-{i}", "chunk_id": None, "export": export, "input": v}
                     for i, v in enumerate(inputs)]

            def run() -> str:
                with tempfile.TemporaryDirectory(prefix="ratchet-probe-") as d:
                    cpath, out = Path(d) / "cases.jsonl", Path(d) / "obs.jsonl"
                    cpath.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
                    code, log = gate._run([*ctx.profile["run_source"], "--cases", str(cpath), "--out", str(out)], ctx.profile_dir, 30)
                    if code != 0 or not out.exists():
                        return f"probe failed: {log[-400:]}"
                    obs = {o["case_id"]: o for o in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
                    return json.dumps([{"input": c["input"], **{k: obs.get(c["case_id"], {}).get(k) for k in ("status", "value", "error_code")}}
                                       for c in cases])

            result = await asyncio.to_thread(run)
            ctx.events.emit("tool.probe_source", actor=actor, payload={"export": export, "input": inputs, "result": result[:4000]})
            return result

        async def submit_ruling(contract_id: str, kind: str, question: str, ruling: str, evidence_refs: str, answer: str) -> str:
            refs = [r.strip() for r in evidence_refs.replace(";", ",").split(",") if r.strip()]
            TEAM.outbox["steward"] = {"contract_id": contract_id, "kind": kind, "question": question, "ruling": ruling,
                                      "evidence_refs": refs, "answer": answer}
            return "Ruling received. Stop now."

        return [ToolSpec("probe_source", "Run the frozen ORIGINAL implementation of one export on up to 8 inputs you choose. inputs_json is a JSON ARRAY of inputs. Returns the real status/value/error_code for each.", probe_source),
                ToolSpec("submit_ruling", "Answer the request; the only way to answer. kind is implementation_clarification | behavior_change | no_decision. evidence_refs is a comma-separated list: the source file path plus the probes or case id you relied on.", submit_ruling)]

    # ── planner ────────────────────────────────────────────────────────────
    def register_planner(self) -> None:
        ctx = self

        async def read_source(chunk_id: str) -> str:
            if chunk_id not in ctx.chunks:
                return f"unknown chunk_id; use one of {sorted(ctx.chunks)}"
            ctx.events.emit("tool.read_source", actor="planner", chunk_id=chunk_id)
            return ctx.source_text(chunk_id)

        async def submit_plan(plan_json: str) -> str:
            try:
                plan = json.loads(plan_json)
            except json.JSONDecodeError as e:
                return f"plan_json is not valid JSON: {e}"
            problems = ctx.check_plan(plan)
            if problems:
                ctx.events.emit("plan.rejected", actor="plan-check", payload={"problems": problems})
                return "PLAN REJECTED by the deterministic plan check. Fix and submit again:\n- " + "\n- ".join(problems)
            TEAM.outbox["planner"] = plan
            return "Plan accepted. Stop now."

        TEAM.register(MemberSpec("planner", "planner", PLANNER_PROMPT, max_iterations=12, tools=[
            ToolSpec("read_source", "Read the source code of one chunk.", read_source),
            ToolSpec("submit_plan", "Hand in the plan as a JSON string. It is validated; a rejected plan comes back with reasons.", submit_plan)],
            submit_tools={"submit_plan"}))

    def check_plan(self, plan: dict) -> list[str]:
        """Deterministic check of the planner's proposal. The route owner's manifests are the ground truth for
        real dependencies: the plan may add ordering constraints (costs parallelism) but may not drop one."""
        problems = []
        entries = {c.get("chunk_id"): c for c in plan.get("chunks", []) if isinstance(c, dict)}
        for cid in self.chunks:
            if cid not in entries:
                problems.append(f"chunk {cid} is missing from the plan")
        for cid, entry in entries.items():
            if cid not in self.chunks:
                problems.append(f"{cid} is not a chunk of this profile")
                continue
            deps = set(entry.get("depends_on") or [])
            if deps - set(self.chunks):
                problems.append(f"{cid} depends on unknown chunk(s) {sorted(deps - set(self.chunks))}")
            missing = set(self.chunks[cid].get("depends_on", [])) - deps
            if missing:
                problems.append(f"{cid} calls code from {sorted(missing)} but the plan does not make it wait for them")
        if not problems:
            done: set = set()
            while len(done) < len(entries):
                ready = [c for c, e in entries.items() if c not in done and set(e.get("depends_on") or []) <= done]
                if not ready:
                    problems.append(f"dependency cycle among {sorted(set(entries) - done)}")
                    break
                done |= set(ready)
        return problems

    def register_solo(self) -> None:
        """Single-agent baseline: ONE member does every chunk and is its own steward.

        Same model as the team's workers, same compile tool, same access to the original via
        probe_source, same gate, attempts and token cap. What it lacks is only what the team adds:
        a second specialist, parallelism, and a shared ledger.
        """
        ctx, member = self, "solo"

        async def check_compile(chunk_id: str, path: str, content: str) -> str:
            if chunk_id not in ctx.chunks:
                return f"unknown chunk_id; use one of {sorted(ctx.chunks)}"
            return await ctx.tool(ctx.worker_tools(member, chunk_id), "check_compile")(path, content)

        async def submit_candidate(chunk_id: str, path: str, content: str, notes: str = "") -> str:
            return await ctx.tool(ctx.worker_tools(member, chunk_id), "submit_candidate")(path, content, notes)

        probe = self.tool(self.steward_tools(actor=member), "probe_source")
        TEAM.register(MemberSpec(member, "solo", SOLO_PROMPT, max_iterations=14, tools=[
            ToolSpec("probe_source", "Run the frozen ORIGINAL implementation of one export on up to 8 inputs you choose. inputs_json is a JSON ARRAY of inputs.", probe),
            ToolSpec("check_compile", "Build ONE file with the real target compiler. Returns BUILD_OK or the compiler errors. Runs no tests.", check_compile),
            ToolSpec("submit_candidate", "Hand in ONE finished file for verification. The only way to hand in work.", submit_candidate)],
            submit_tools={"submit_candidate"}, is_done=lambda: ctx.handed_in(member)))

    @staticmethod
    def tool(specs: list[ToolSpec], name: str):
        return next(t.func for t in specs if t.name == name)

    def register_team(self, steward_model: str | None) -> None:
        TEAM.register(MemberSpec("steward", "steward", STEWARD_PROMPT, model=steward_model, tools=self.steward_tools(),
                                 max_iterations=10, serial=True, submit_tools={"submit_ruling"}))
        for cid in self.chunks:
            member = f"worker-{cid}"
            TEAM.register(MemberSpec(member, "worker", WORKER_PROMPT, tools=self.worker_tools(member, cid), max_iterations=10,
                                     submit_tools={"submit_candidate"},
                                     is_done=lambda member=member, cid=cid: self.handed_in(member, cid)))
