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
    "function. Name the exact target-language construct to use. (5) Probe several inputs in ONE probe_source call. "
    "(6) Finish every request by calling submit_ruling exactly once; that is the only way to answer."
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

    def mark_seen(self, member: str, cid: str) -> None:
        self.seen[member] = self.ledger.hashes(self.chunks[cid].get("contract_ids", []))

    # ── steward ────────────────────────────────────────────────────────────
    def steward_query(self, cid: str, *, asked_by: str, question: str = "", counterexample: dict | None = None,
                      candidate_files: list | None = None) -> str:
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

    def steward_tools(self) -> list[ToolSpec]:
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
            ctx.events.emit("tool.probe_source", actor="contract-steward", payload={"export": export, "input": inputs, "result": result[:600]})
            return result

        async def submit_ruling(contract_id: str, kind: str, question: str, ruling: str, evidence_refs: str, answer: str) -> str:
            refs = [r.strip() for r in evidence_refs.replace(";", ",").split(",") if r.strip()]
            TEAM.outbox["steward"] = {"contract_id": contract_id, "kind": kind, "question": question, "ruling": ruling,
                                      "evidence_refs": refs, "answer": answer}
            return "Ruling received. Stop now."

        return [ToolSpec("probe_source", "Run the frozen ORIGINAL implementation of one export on up to 8 inputs you choose. inputs_json is a JSON ARRAY of inputs. Returns the real status/value/error_code for each.", probe_source),
                ToolSpec("submit_ruling", "Answer the request; the only way to answer. kind is implementation_clarification | behavior_change | no_decision. evidence_refs is a comma-separated list: the source file path plus the probes or case id you relied on.", submit_ruling)]

    def register_team(self, steward_model: str | None) -> None:
        TEAM.register(MemberSpec("steward", "steward", STEWARD_PROMPT, model=steward_model, tools=self.steward_tools(),
                                 max_iterations=10, serial=True))
        for cid in self.chunks:
            member = f"worker-{cid}"
            TEAM.register(MemberSpec(member, "worker", WORKER_PROMPT, tools=self.worker_tools(member, cid), max_iterations=10))
