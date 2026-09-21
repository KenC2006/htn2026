"""The only things a team member can do besides think. Each tool is executed by the engine.

Worker tools:   check_compile (build only; reveals nothing about tests), ask_steward (agent-to-agent question)
Steward tools:  probe_source (run the frozen ORIGINAL implementation on inputs the steward chooses)

No tool exposes the test cases, expected outputs, the file system, a shell, or credentials.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from typing import Union

from ..framework.team import TEAM, MemberSpec, ToolSpec
from . import gate
from .contracts import ContractLedger, DecisionRejected
from .domain import coerce, in_domain
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

HINT_PROMPT = (
    "You are the expert on a code-migration team. A worker's code was rejected: it does not compile, or it crashes. "
    "You get the code and the error. Reply with a short, specific fix: which lines are wrong, why, and what to write instead. "
    "At most 12 lines. Do not rewrite the whole file. Do not change what the function returns."
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

TESTER_PROMPT = (
    "You are the tester of a code-migration team. Your job is to BREAK new code: find inputs on which it gives a different "
    "result from the original. You never write expected outputs; the original code is run to get the truth. Method: read both "
    "versions, think about where the two languages behave differently (negative numbers with division and modulo, integer "
    "overflow and very large values, zero, boundaries, rounding, empty or unusual strings) and about shortcuts in the new code "
    "(special-cased values, lookup tables, hardcoded answers: try values NEAR them, not the same ones). Put many varied inputs in "
    "ONE try_inputs call; you get 3 calls per chunk. Stop as soon as you find a difference. Do not waste tries on inputs the "
    "original itself rejects. Finish by calling submit_report exactly once."
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

def _loads(value) -> tuple[object, str]:
    """Models are told to pass JSON as a string, and about one call in twenty passes the list or object itself.
    Refusing that cost the agent a turn each time, so both are taken. Returns (value, '' or what is wrong with it)."""
    if not isinstance(value, str):
        return value, ""
    try:
        return json.loads(value), ""
    except json.JSONDecodeError as e:
        return None, f"not valid JSON: {e}"


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
    under_test: dict = field(default_factory=dict)   # chunk -> (candidate, overlay) the tester may attack right now
    tester_calls: dict = field(default_factory=dict)
    found: dict = field(default_factory=dict)        # chunk -> cases the tester found in the current hunt
    _cases_lock: threading.Lock = field(default_factory=threading.Lock)
    _seq_lock: threading.Lock = field(default_factory=threading.Lock)
    _found: int = 0
    probe_calls: int = 0                             # probes used by the expert in its current request
    _n: int = 0

    def seq(self) -> int:
        with self._seq_lock:
            self._n += 1
            return self._n

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
        self.probe_calls = 0
        parts = [f"Request from {asked_by} about chunk {cid} (exports {m['exports']}).",
                 f"Contracts in play:\n{self.ledger.prompt_text(m['contract_ids'])}",
                 f"Source:\n{self.source_text(cid)}"]
        if question:
            parts.append(f"Worker's question: {question}")
        if counterexample:
            ce = {**counterexample, "input": json.dumps(counterexample.get("input"))[:800]}
            parts.append("The verifier rejected a candidate. Counterexample (source output is the truth): "
                         f"{json.dumps(ce)}\nRejected candidate: {json.dumps(candidate_files)[:4000]}\n"
                         "Decide whether this shows a general source-vs-target difference other workers could also hit.")
        if build_error:
            parts.append("A candidate that FOLLOWED your guidance did not compile. The real compiler said:\n"
                         f"{build_error[-900:]}\nCandidate: {json.dumps(candidate_files)[:4000]}\n"
                         "If your guidance named a construct that does not exist in the target language or was otherwise "
                         "wrong, issue a corrected implementation_clarification that replaces it and say which decision it "
                         "corrects. If the worker simply made its own mistake, use no_decision.")
        parts.append(f"probe_source takes export (one of {sorted({e for c in self.chunks.values() for e in c['exports']})}) "
                     f"and inputs_json, a JSON array whose items are shaped like: {self._example_input(cid)}.\n"
                     + (f"Only inputs inside this range have to match, and probes outside it are refused: {json.dumps(m['input_domain'])}. "
                        "Two or three probe calls are enough; do not explore further.\n" if m.get("input_domain") else "") +
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
            n_seq = ctx.seq()
            v = await asyncio.to_thread(
                gate.check, ctx.profile_dir, cid, {"files": [{"path": path, "content": content}]}, ctx.cases_path,
                ctx.run_dir, attempt_id=f"{cid}:compile-{n_seq}", overlay=ctx.integ.files_of(ctx.chunks[cid].get("depends_on", [])),
                stop_after_build=True)
            ctx.events.emit("tool.check_compile", actor=member, chunk_id=cid,
                            payload={"result": v.status, "path": path, "content": content[:20000],
                                     "error": "" if v.status == "BUILD_OK" else v.detail[:600]})
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
            ctx.events.emit("worker.wrote", actor=member, chunk_id=cid, payload={"path": path, "content": content[:20000]})
            return f"Received {path}. Submit any other file you are allowed to write, otherwise stop now; the verifier takes it from here."

        return [ToolSpec("submit_candidate", "Hand in ONE finished file for verification. This is the only way to hand in work.", submit_candidate),
                ToolSpec("check_compile", "Build ONE file of yours with the real target compiler. Returns BUILD_OK or the compiler errors. Runs no tests.", check_compile),
                ToolSpec("ask_steward", "Ask the contract steward how a source behavior must be reproduced in the target language. Returns a binding answer.", ask_steward)]

    def steward_tools(self, actor: str = "contract-steward") -> list[ToolSpec]:
        ctx = self

        async def probe_source(export: str, inputs_json: Union[str, list, dict]) -> str:
            inputs, bad = _loads(inputs_json)
            if bad:
                return f"inputs_json is {bad}"
            inputs = (inputs if isinstance(inputs, list) else [inputs])[:8]
            ctx.probe_calls += 1
            if ctx.probe_calls > 4:                                      # an expert that keeps probing never rules
                return "You have used your 4 probes for this request. Rule now from what you have seen: call submit_ruling."
            owner = next((c for c, m in ctx.chunks.items() if export in m.get("exports", [])), None)
            if owner and ctx.chunks[owner].get("input_domain"):          # only behavior inside the declared range matters
                refused = [f"{json.dumps(v)}: {why}" for v in inputs if isinstance(v, dict) and (why := ctx.outside_domain(owner, v))]
                inputs = [v for v in inputs if isinstance(v, dict) and not ctx.outside_domain(owner, v)]
                if not inputs:
                    return ("Every input was outside the declared input range, which is all that has to match. Allowed: "
                            f"{json.dumps(ctx.chunks[owner]['input_domain'])}. Refused: {refused[:3]}")
            cases = [{"schema_version": 1, "case_id": f"probe-{i}", "chunk_id": None, "export": export, "input": v}
                     for i, v in enumerate(inputs)]

            def run() -> str:
                with tempfile.TemporaryDirectory(prefix="parity-probe-") as d:
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
            if len(result) > 4000:                                       # one huge output once made a single prompt millions of tokens long
                return result[:4000] + " … (cut: outputs this large cannot be shown; probe smaller inputs)"
            return result

        async def submit_ruling(contract_id: str, kind: str, question: str, ruling: str, evidence_refs: Union[str, list], answer: str) -> str:
            refs = ([str(r).strip() for r in evidence_refs if str(r).strip()] if isinstance(evidence_refs, list)
                    else [r.strip() for r in evidence_refs.replace(";", ",").split(",") if r.strip()])
            TEAM.outbox["steward"] = {"contract_id": contract_id, "kind": kind, "question": question, "ruling": ruling,
                                      "evidence_refs": refs, "answer": answer}
            return "Ruling received. Stop now."

        return [ToolSpec("probe_source", "Run the frozen ORIGINAL implementation of one export on up to 8 inputs you choose. inputs_json is a JSON ARRAY of inputs. Returns the real status/value/error_code for each.", probe_source),
                ToolSpec("submit_ruling", "Answer the request; the only way to answer. kind is implementation_clarification | behavior_change | no_decision. evidence_refs is a comma-separated list: the source file path plus the probes or case id you relied on.", submit_ruling)]

    # ── tester ─────────────────────────────────────────────────────────────
    def add_found_case(self, cid: str, value: dict) -> dict:
        """A tester input on which the candidate and the original disagreed joins the run's cases for good."""
        with self._cases_lock:
            if not self._found:   # a resumed run already has found cases: a repeated case_id blocks every later check
                self._found = self.cases_path.read_text(encoding="utf-8").count('"found_by"')
            self._found += 1
            case ={"schema_version": 1, "case_id": f"found-{cid}-{self._found}", "chunk_id": cid,
                    "export": self.chunks[cid]["exports"][0], "input": value, "found_by": "tester"}
            with self.cases_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(case) + "\n")
            return case

    def outside_domain(self, cid: str, value: dict) -> str:
        """Why an input is outside the chunk's declared input domain ('' if it is inside). Plain code, not the tester's opinion."""
        for key, rule in (self.chunks[cid].get("input_domain") or {}).items():
            why = in_domain(rule, value.get(key))
            if why:
                return f"{key}={json.dumps(value.get(key))} {why}"
        return ""

    def tester_query(self, cid: str, candidate: dict) -> str:
        m = self.chunks[cid]
        files = "".join(f"\n# {f['path']}\n{f['content']}" for f in candidate["files"])
        return (f"New code for chunk {cid} (export {m['exports'][0]}) just matched the original on the fixed test cases. "
                f"Find inputs where it does NOT match.\n\nOriginal:\n{self.source_text(cid)}\n\nNew code:{files}\n\n"
                f"Input shape (one example, keep exactly these keys and types): {json.dumps(m.get('example_input', {}))}\n"
                f"Allowed input range (inputs outside it are refused and do not count): {json.dumps(m.get('input_domain') or 'not declared')}\n"
                "Keep every input small (a list needs at most a dozen items): small inputs expose the same differences and big ones only cost tokens.\n"
                f"Call try_inputs with chunk_id=\"{cid}\". Then call submit_report.")

    def register_tester(self, model: str | None = None) -> None:
        ctx = self

        async def try_inputs(chunk_id: str, inputs_json: Union[str, list, dict]) -> str:
            if chunk_id not in ctx.under_test:
                return f"nothing is under test for {chunk_id}; use one of {sorted(ctx.under_test)}"
            if ctx.tester_calls.get(chunk_id, 0) >= 3:
                return "You have used your 3 tries for this chunk. Call submit_report now."
            inputs, bad = _loads(inputs_json)
            if bad:
                return f"inputs_json is {bad}"
            keys = set(ctx.chunks[chunk_id].get("example_input", {}))
            inputs = [v for v in (inputs if isinstance(inputs, list) else [inputs]) if isinstance(v, dict) and (not keys or set(v) == keys)][:12]
            refused = [f"{json.dumps(v)}: {why}" for v in inputs if (why := ctx.outside_domain(chunk_id, v))]
            inputs = [v for v in inputs if not ctx.outside_domain(chunk_id, v)]
            if not inputs:
                return (f"No usable inputs. Each input must be an object with exactly these keys: {sorted(keys)}, inside the allowed range. "
                        f"Refused: {refused[:4]}")
            ctx.tester_calls[chunk_id] = n = ctx.tester_calls.get(chunk_id, 0) + 1
            rules = ctx.chunks[chunk_id].get("input_domain") or {}
            inputs = [{k: coerce(rules.get(k, {}), v) for k, v in one.items()} for one in inputs]
            candidate, overlay = ctx.under_test[chunk_id]
            export = ctx.chunks[chunk_id]["exports"][0]
            cases = [{"schema_version": 1, "case_id": f"try-{i}", "chunk_id": chunk_id, "export": export, "input": v} for i, v in enumerate(inputs)]

            def run() -> tuple[str, list]:
                with tempfile.TemporaryDirectory(prefix="parity-try-") as d:
                    cpath = Path(d) / "cases.jsonl"
                    cpath.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
                    n_seq = ctx.seq()
                    v = gate.check(ctx.profile_dir, chunk_id, candidate, cpath, ctx.run_dir, attempt_id=f"{chunk_id}:tester-{n_seq}", overlay=overlay)
                    if v.status not in ("ACCEPTED", "REJECTED_BEHAVIOR"):
                        return f"Could not compare ({v.status}: {v.detail[:300]}). Inputs the original itself rejects do not count; try others.", []
                    obs = ctx.run_dir / "observations" / f"{chunk_id}-tester-{n_seq}"
                    read = lambda p: {o["case_id"]: o for o in map(json.loads, (obs / p).read_text(encoding="utf-8").splitlines())}  # noqa: E731
                    src, tgt = read("source_obs.jsonl"), read("target_obs.jsonl")
                    show = lambda o: o.get("value") if o.get("status") == "ok" else o.get("error_code") or o.get("status")  # noqa: E731
                    rows = [{"input": c["input"], "original": show(src[c["case_id"]]), "new": show(tgt[c["case_id"]]),
                             "differs": c["case_id"] in v.mismatched_case_ids} for c in cases]
                    return "", rows

            problem, rows = await asyncio.to_thread(run)
            if problem:
                return problem
            differing = [r for r in rows if r["differs"]]
            for r in differing:
                ctx.found.setdefault(chunk_id, []).append(ctx.add_found_case(chunk_id, r["input"]))
            ctx.events.emit("tool.try_inputs", actor="tester", chunk_id=chunk_id,
                            payload={"export": export, "tried": len(rows), "differ": len(differing),
                                     "rows": [r for r in rows[:12] if len(json.dumps(r)) < 2000]})
            # The tester wrote the inputs itself; echoing big batches back cost 278k tokens on the first records run.
            if differing:
                return (f"{len(differing)} of {len(rows)} inputs DIFFER. They are now permanent test cases. Call submit_report.\n"
                        + json.dumps(differing)[:3000])
            return f"All {len(rows)} inputs match ({3 - n} tries left)."

        def reporter(member: str):
            async def submit_report(chunk_id: str, summary: str) -> str:
                TEAM.outbox[member] = {"chunk_id": chunk_id, "summary": summary}
                return "Report received. Stop now."
            return submit_report

        for member in ["tester", *(f"tester-{c}" for c in self.chunks)]:
            TEAM.register(MemberSpec(member, "tester", TESTER_PROMPT, model=model, max_iterations=10, fresh_each_turn=True, tools=[
                ToolSpec("try_inputs", "Run up to 12 inputs through BOTH the original and the new code. inputs_json is a JSON ARRAY of input objects. Returns both outputs for each and which differ. At most 3 calls per chunk.", try_inputs),
                ToolSpec("submit_report", "Finish: say in one or two sentences what you attacked and what you found. The only way to finish.", reporter(member))],
                submit_tools={"submit_report"}))

    # ── planner ────────────────────────────────────────────────────────────
    def register_planner(self) -> None:
        ctx = self

        async def read_source(chunk_id: str) -> str:
            if chunk_id not in ctx.chunks:
                return f"unknown chunk_id; use one of {sorted(ctx.chunks)}"
            ctx.events.emit("tool.read_source", actor="planner", chunk_id=chunk_id)
            return ctx.source_text(chunk_id)

        async def submit_plan(plan_json: Union[str, dict]) -> str:
            plan, bad = _loads(plan_json)
            if bad or not isinstance(plan, dict):
                return f"plan_json is {bad or 'not a JSON object'}"
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

    @staticmethod
    def tool(specs: list[ToolSpec], name: str):
        return next(t.func for t in specs if t.name == name)

    def register_team(self, steward_model: str | None) -> None:
        # The expert again, without tools: it reads a compiler error and tells the worker how to fix it. It writes no code files.
        for member in ["expert-hint", *(f"expert-hint-{c}" for c in self.chunks)]:
            TEAM.register(MemberSpec(member, "steward", HINT_PROMPT, model=steward_model, max_iterations=2, fresh_each_turn=True))
        TEAM.register(MemberSpec("steward", "steward", STEWARD_PROMPT, model=steward_model, tools=self.steward_tools(),
                                 max_iterations=8, serial=True, fresh_each_turn=True, submit_tools={"submit_ruling"}))
        for cid in self.chunks:
            member = f"worker-{cid}"
            TEAM.register(MemberSpec(member, "worker", WORKER_PROMPT, tools=self.worker_tools(member, cid), max_iterations=10,
                                     submit_tools={"submit_candidate"},
                                     is_done=lambda member=member, cid=cid: self.handed_in(member, cid)))
