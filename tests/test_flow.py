"""Ledger + integrator checks, no model calls. Uses the real rustc.

    python -m unittest tests.test_flow -v
"""
import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from parity.engine import gate
from parity.engine.contracts import ContractLedger, DecisionRejected
from parity.engine.events import EventLog
from parity.engine.integrator import Integrator
from parity.engine.tools import RunContext
from parity.framework.team import TEAM

FIXTURE = Path(__file__).parent / "flow_fixture"
BUCKET = "pub fn bucket(ts: i64, width: i64) -> i64 {\n    ts.div_euclid(width) * width\n}\n"
OFFSET = "pub fn offset(ts: i64, width: i64) -> i64 {\n    ts.rem_euclid(width)\n}\n"
SPLIT = "pub fn split(ts: i64, width: i64) -> (i64, i64) {\n    (crate::bucket::bucket(ts, width), crate::offset::offset(ts, width))\n}\n"
BAD_BUCKET = "pub fn bucket(ts: i64, width: i64) -> i64 {\n    ts / width * width\n}\n"
PROPOSAL = {"contract_id": "time-arithmetic", "kind": "implementation_clarification", "question": "negative ts?",
            "ruling": "Use div_euclid / rem_euclid.", "evidence_refs": ["legacy/timeparts.py", "bucket-neg-one"]}


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="parity-flow-"))
        self.profile = self.tmp / "profile"
        shutil.copytree(FIXTURE, self.profile)
        gate.freeze(self.profile)
        self.run_dir = self.tmp / "run"
        self.chunks = {p.stem: json.loads(p.read_text()) for p in (self.profile / "chunks").glob("*.json")}
        self.events = EventLog(self.run_dir, "t")
        self.ledger = ContractLedger(self.profile, self.run_dir, self.chunks, self.events)
        self.integ = Integrator(self.profile, self.run_dir, self.profile / "cases.jsonl", self.ledger, self.events)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cand(self, path, content):
        return {"files": [{"path": path, "content": content}], "contract_hashes": self.ledger.hashes()}

    def test_decision_bumps_version_and_names_dependent_closure(self):
        before = self.ledger.hashes()
        d = self.ledger.record_decision(PROPOSAL, proposed_by="contract-steward", allowed_contracts=["time-arithmetic"])
        self.assertEqual((d["version"], d["supersedes"], d["affected_chunks"]), (2, 1, ["S1", "S2", "S3"]))
        self.assertNotEqual(before, self.ledger.hashes())
        self.assertIn("div_euclid", self.ledger.prompt_text(["time-arithmetic"]))

    def test_steward_cannot_change_behavior_or_touch_other_contracts(self):
        for bad in ({**PROPOSAL, "kind": "behavior_change"}, {**PROPOSAL, "contract_id": "nope"},
                    {**PROPOSAL, "evidence_refs": []}, {**PROPOSAL, "ruling": " "}):
            with self.assertRaises(DecisionRejected):
                self.ledger.record_decision(bad, proposed_by="contract-steward", allowed_contracts=["time-arithmetic"])
        self.assertEqual(self.ledger.contracts["time-arithmetic"]["version"], 1)

    def test_candidate_written_under_old_contract_is_stale_at_integration(self):
        old = self.cand("target/offset.rs", OFFSET)
        self.ledger.record_decision(PROPOSAL, proposed_by="contract-steward", allowed_contracts=["time-arithmetic"])
        self.assertEqual(self.integ.integrate("S2", old).status, "STALE")
        self.assertEqual(self.integ.accepted, [])
        self.assertEqual(self.integ.integrate("S2", self.cand("target/offset.rs", OFFSET)).status, "ACCEPTED")

    def test_full_tree_integration_and_receipts(self):
        for cid, path, code in (("S1", "target/bucket.rs", BUCKET), ("S2", "target/offset.rs", OFFSET), ("S3", "target/split.rs", SPLIT)):
            v = self.integ.integrate(cid, self.cand(path, code))
            self.assertEqual(v.status, "ACCEPTED", v.detail)
        receipt = json.loads((self.run_dir / "receipts" / "S3.json").read_text())
        self.assertEqual(receipt["cases"]["passed"], 27)  # S3's integration re-ran S1 and S2's cases too
        self.assertEqual(receipt["accepted_tree"], self.integ.tree_hash())
        self.assertEqual((self.run_dir / "accepted" / "target" / "split.rs").read_text(), SPLIT)

    def test_dependent_fails_integration_when_dependency_is_still_a_placeholder(self):
        v = self.integ.integrate("S3", self.cand("target/split.rs", SPLIT))
        self.assertEqual(v.status, "REJECTED_INTEGRATION")

    def test_replacing_an_accepted_chunk_with_a_regression_is_rejected_and_tree_unchanged(self):
        self.assertTrue(self.integ.integrate("S1", self.cand("target/bucket.rs", BUCKET)).accepted)
        tree = self.integ.tree_hash()
        self.assertEqual(self.integ.integrate("S1", self.cand("target/bucket.rs", BAD_BUCKET)).status, "REJECTED_INTEGRATION")
        self.assertEqual(self.integ.tree_hash(), tree)

    def test_contract_change_marks_accepted_receipts_stale_and_blocks_export(self):
        self.assertTrue(self.integ.integrate("S1", self.cand("target/bucket.rs", BUCKET)).accepted)
        d = self.ledger.record_decision(PROPOSAL, proposed_by="contract-steward", allowed_contracts=["time-arithmetic"])
        self.assertEqual(self.integ.invalidate(d["affected_chunks"], "contract changed"), ["S1"])
        self.assertFalse(self.integ.exportable)
        self.assertEqual(json.loads((self.run_dir / "receipts" / "S1.json").read_text())["status"], "STALE")
        self.assertTrue(self.integ.integrate("S1", self.cand("target/bucket.rs", BUCKET)).accepted)  # revalidated
        self.assertTrue(self.integ.exportable)

    # ── member tools (no model calls) ──
    def ctx(self):
        profile = json.loads((self.profile / "profile.json").read_text())
        return RunContext(self.profile, self.run_dir, self.profile / "cases.jsonl", profile, self.chunks, self.ledger, self.integ, self.events)

    def test_tester_inputs_outside_the_declared_range_are_refused_and_found_inputs_become_cases(self):
        ctx = self.ctx()
        self.assertEqual(ctx.outside_domain("S1", {"ts": -5, "width": 1000}), "")
        self.assertIn("width", ctx.outside_domain("S1", {"ts": 5, "width": -1000}))       # negative width was never supported
        self.assertIn("ts", ctx.outside_domain("S1", {"ts": -9223372036854775808, "width": 3}))
        self.assertIn("number", ctx.outside_domain("S1", {"ts": "5", "width": 3}))
        ctx.cases_path = self.run_dir / "cases.jsonl"                                     # the run's own copy, never the frozen file
        ctx.cases_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.cases_path.write_bytes((self.profile / "cases.jsonl").read_bytes())
        case = ctx.add_found_case("S1", {"ts": -1001, "width": 1000})
        lines = [json.loads(l) for l in ctx.cases_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[-1], case)
        self.assertEqual((case["chunk_id"], case["export"], case["found_by"]), ("S1", "bucket", "tester"))
        self.assertNotIn("found-", (self.profile / "cases.jsonl").read_text(encoding="utf-8"))

    @staticmethod
    def tool(specs, name):
        return next(t.func for t in specs if t.name == name)

    def test_steward_probe_runs_the_real_original_in_one_batch(self):
        probe = self.tool(self.ctx().steward_tools(), "probe_source")
        out = json.loads(asyncio.run(probe("offset", json.dumps([{"ts": -1, "width": 1000}, {"ts": 2500, "width": 1000}]))))
        self.assertEqual([o["value"] for o in out], [999, 500])
        self.assertIn("not valid JSON", asyncio.run(probe("offset", "{nope")))

    def test_worker_compile_tool_builds_but_reveals_no_test_results(self):
        tools = self.ctx().worker_tools("worker-S1", "S1")
        compile_ = self.tool(tools, "check_compile")
        self.assertEqual(asyncio.run(compile_("target/bucket.rs", BAD_BUCKET)), "BUILD_OK")  # wrong, but it compiles
        self.assertIn("REJECTED_BUILD", asyncio.run(compile_("target/bucket.rs", "pub fn bucket() {")))
        self.assertIn("REJECTED_POLICY", asyncio.run(compile_("runners/target.py", "x")))

    def test_submit_tools_fill_the_outbox(self):
        TEAM.reset("t")
        ctx = self.ctx()
        submit = self.tool(ctx.worker_tools("worker-S1", "S1"), "submit_candidate")
        asyncio.run(submit("target/bucket.rs", "v1")); asyncio.run(submit("target/bucket.rs", BUCKET, "done"))
        self.assertEqual(TEAM.outbox["worker-S1"]["files"], [{"path": "target/bucket.rs", "content": BUCKET}])
        rule = self.tool(ctx.steward_tools(), "submit_ruling")
        asyncio.run(rule("time-arithmetic", "implementation_clarification", "q", "Use div_euclid.", "legacy/timeparts.py, probe", "a"))
        d = ctx.apply_proposal("S1", TEAM.outbox["steward"], asked_by="worker-S1")
        self.assertEqual((d["version"], d["evidence_refs"]), (2, ["legacy/timeparts.py", "probe"]))

    def test_behavior_change_from_steward_blocks_the_chunk_for_a_human(self):
        ctx = self.ctx()
        self.assertIsNone(ctx.apply_proposal("S1", {**PROPOSAL, "kind": "behavior_change", "question": "is -0 allowed?"}, asked_by="worker-S1"))
        self.assertIn("needs a human", ctx.blocked["S1"])
        self.assertEqual(self.ledger.contracts["time-arithmetic"]["version"], 1)

    # ── planner ──
    def test_plan_check_accepts_a_sound_plan_and_rejects_dropped_dependencies_and_cycles(self):
        ctx = self.ctx()
        good = {"chunks": [{"chunk_id": "S1", "depends_on": []}, {"chunk_id": "S2", "depends_on": []},
                           {"chunk_id": "S3", "depends_on": ["S1", "S2"]}]}
        self.assertEqual(ctx.check_plan(good), [])
        extra = {"chunks": [{"chunk_id": "S1", "depends_on": []}, {"chunk_id": "S2", "depends_on": ["S1"]},
                            {"chunk_id": "S3", "depends_on": ["S1", "S2"]}]}
        self.assertEqual(ctx.check_plan(extra), [])  # over-cautious ordering is allowed; it only costs parallelism
        dropped = {"chunks": [{"chunk_id": "S1", "depends_on": []}, {"chunk_id": "S2", "depends_on": []},
                              {"chunk_id": "S3", "depends_on": ["S1"]}]}
        self.assertIn("does not make it wait", " ".join(ctx.check_plan(dropped)))
        self.assertIn("missing from the plan", " ".join(ctx.check_plan({"chunks": good["chunks"][:2]})))
        cyc = {"chunks": [{"chunk_id": "S1", "depends_on": ["S3"]}, {"chunk_id": "S2", "depends_on": []},
                          {"chunk_id": "S3", "depends_on": ["S1", "S2"]}]}
        self.assertIn("cycle", " ".join(ctx.check_plan(cyc)))

    def test_planner_submit_tool_returns_the_rejection_reasons_to_the_agent(self):
        TEAM.reset("t")
        ctx = self.ctx()
        ctx.register_planner()
        submit = self.tool(TEAM.specs["planner"].tools, "submit_plan")
        reply = asyncio.run(submit(json.dumps({"chunks": [{"chunk_id": "S3", "depends_on": []}]})))
        self.assertIn("PLAN REJECTED", reply)
        self.assertNotIn("planner", TEAM.outbox)
        ok = {"chunks": [{"chunk_id": c, "depends_on": self.chunks[c]["depends_on"]} for c in self.chunks], "risks": []}
        self.assertIn("accepted", asyncio.run(submit(json.dumps(ok))))
        self.assertEqual(TEAM.outbox["planner"], ok)

    # ── resume ──
    def test_resume_reuses_accepted_chunks_only_while_their_evidence_still_holds(self):
        fixture_hash = json.loads((self.profile / gate.LOCK_NAME).read_text())["fixture_hash"]
        self.assertTrue(self.integ.integrate("S1", self.cand("target/bucket.rs", BUCKET)).accepted)
        self.assertTrue(self.integ.integrate("S2", self.cand("target/offset.rs", OFFSET)).accepted)

        def restarted():  # a new process: fresh ledger and integrator over the same run folder
            ledger = ContractLedger(self.profile, self.run_dir, self.chunks, self.events)
            return ledger, Integrator(self.profile, self.run_dir, self.profile / "cases.jsonl", ledger, self.events)

        _, integ = restarted()
        self.assertEqual(integ.load_existing(self.chunks, fixture_hash), ["S1", "S2"])
        self.assertTrue(integ.integrate("S3", {"files": [{"path": "target/split.rs", "content": SPLIT}],
                                               "contract_hashes": self.ledger.hashes()}).accepted)  # builds on the reused tree
        _, integ = restarted()
        self.assertEqual(integ.load_existing(self.chunks, "a-different-fixture"), [])
        # A decision recorded just before the crash: receipts issued under the old version are not reused.
        self.ledger.record_decision(PROPOSAL, proposed_by="contract-steward", allowed_contracts=["time-arithmetic"])
        _, integ = restarted()
        self.assertEqual(integ.load_existing(self.chunks, fixture_hash), [])

    # ── locked evaluation ──
    def _accept_tree(self, offset_code=OFFSET):
        self.events.emit("run.started", actor="scheduler", payload={"chunks": ["S1", "S2", "S3"], "profile_dir": str(self.profile)})
        for cid, path, code in (("S1", "target/bucket.rs", BUCKET), ("S2", "target/offset.rs", offset_code), ("S3", "target/split.rs", SPLIT)):
            self.assertTrue(self.integ.integrate(cid, self.cand(path, code)).accepted)
        self.events.emit("run.finished", actor="scheduler", payload={"accepted": self.integ.accepted, "exportable": True, "accepted_tree": self.integ.tree_hash()})

    def test_locked_evaluation_passes_a_correct_tree_and_is_single_use(self):
        from parity.engine.evaluate import locked_evaluate
        self._accept_tree()
        summary = locked_evaluate(self.run_dir, self.profile)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual({c: r["cases"]["passed"] for c, r in summary["chunks"].items()}, {"S1": 100, "S2": 100, "S3": 100})
        self.assertEqual(locked_evaluate(self.run_dir, self.profile)["status"], "ALREADY_RUN")
        self.assertFalse((self.run_dir / "candidates" / "S1-locked-eval" / "locked").exists())  # hidden from the candidate

    def test_locked_evaluation_catches_what_development_cases_missed(self):
        from parity.engine.evaluate import locked_evaluate
        # Passes all 27 development cases (none uses width 7), but is wrong.
        sneaky = "pub fn offset(ts: i64, width: i64) -> i64 {\n    if width == 7 { ts % width } else { ts.rem_euclid(width) }\n}\n"
        self._accept_tree(offset_code=sneaky)
        summary = locked_evaluate(self.run_dir, self.profile)
        self.assertEqual(summary["status"], "FAIL")
        self.assertEqual(summary["chunks"]["S1"]["status"], "PASS")
        self.assertEqual(summary["chunks"]["S2"]["status"], "FAIL")
        self.assertEqual(summary["chunks"]["S2"]["first_failure"]["input"]["width"], 7)
        self.assertEqual(json.loads((self.run_dir / "receipts" / "S2.json").read_text())["status"], "FAILED_LOCKED_EVALUATION")
        code, _ = self._cli("export", run_id="run", profile_dir=None)
        self.assertEqual(code, 1)
        self.assertIn("Locked evaluation (cases never used for repair): **FAIL**", (self.run_dir / "export" / "report.md").read_text())

    def test_handed_in_requires_every_allowlisted_file(self):
        TEAM.reset("t")
        ctx = self.ctx()
        self.assertFalse(ctx.handed_in("worker-S1", "S1"))
        asyncio.run(self.tool(ctx.worker_tools("worker-S1", "S1"), "submit_candidate")("target/bucket.rs", BUCKET))
        self.assertTrue(ctx.handed_in("worker-S1", "S1"))
        self.assertFalse(ctx.handed_in("worker-S1", "S2"))

    def test_member_with_a_poisoned_conversation_gets_one_fresh_start(self):
        # Regression: one malformed tool call made the provider reject every later call of that member (HTTP 400).
        from types import SimpleNamespace
        from parity.framework.team import MemberSpec
        TEAM.reset("t")
        TEAM.register(MemberSpec("solo", "solo", "prompt"))
        built = []

        async def fake_agent(member):
            if member not in TEAM._agents:
                n = len(built)

                async def invoke(inputs, n=n):
                    if n == 0:
                        raise RuntimeError("Error code: 400 - Provider returned error")
                    return {"output": f"ok from conversation {inputs['conversation_id']}"}

                built.append(n)
                gen = TEAM._generation.get(member, 0)
                TEAM._agents[member] = SimpleNamespace(invoke=invoke, card=SimpleNamespace(id=f"t.{member}" + (f".r{gen}" if gen else "")))
            return TEAM._agents[member]

        TEAM._agent = fake_agent
        try:
            text, _, _ = asyncio.run(TEAM.ask("solo", "go"))
        finally:
            del TEAM._agent
        self.assertEqual(text, "ok from conversation t.solo.r1")
        self.assertEqual([r["member"] for r in TEAM.restarts], ["solo"])

    def test_a_dropped_connection_is_waited_out_in_the_same_conversation(self):
        # Regression: a few seconds without network made four pieces spend all their tries on connection errors.
        from types import SimpleNamespace
        from unittest import mock
        from parity.framework.team import MemberSpec
        TEAM.reset("t")
        TEAM.register(MemberSpec("solo", "solo", "prompt"))
        calls, waits = [], []

        async def invoke(inputs):
            calls.append(inputs["conversation_id"])
            if len(calls) < 3:
                raise RuntimeError("[181001] model call failed, reason: openAI API async invoke error: APIConnectionError: Connection error.")
            return {"output": "ok"}

        async def fake_agent(member):
            return SimpleNamespace(invoke=invoke, card=SimpleNamespace(id="t.solo"))

        async def fake_sleep(seconds):
            waits.append(seconds)

        TEAM._agent = fake_agent
        try:
            with mock.patch("parity.framework.team.asyncio.sleep", fake_sleep):
                text, _, _ = asyncio.run(TEAM.ask("solo", "go"))
        finally:
            del TEAM._agent
        self.assertEqual(text, "ok")
        self.assertEqual(calls, ["t.solo"] * 3, "same conversation every time: the member keeps its memory")
        self.assertEqual(waits, [5, 15])
        self.assertEqual(TEAM.restarts, [], "a network error is not a poisoned conversation")

    # ── CLI (no model calls) ──
    def _cli(self, fn, **kw):
        import argparse, contextlib, io
        from parity import cli
        cli.RUNS = self.tmp  # the run lives at <tmp>/run
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = getattr(cli, fn)(argparse.Namespace(**kw))
        return code, buf.getvalue()

    def test_scan_accepts_the_fixture_and_flags_a_missing_placeholder(self):
        code, out = self._cli("scan", profile_dir=str(self.profile))
        self.assertEqual(code, 0, out)
        (self.profile / "target" / "split.rs").unlink()
        code, out = self._cli("scan", profile_dir=str(self.profile))
        self.assertEqual(code, 1)
        self.assertIn("no compiling placeholder at target/split.rs", out)

    def test_export_writes_patch_and_report_and_refuses_a_stale_run(self):
        self.events.emit("run.started", actor="scheduler", payload={"chunks": ["S1", "S2", "S3"], "profile_dir": str(self.profile)})
        for cid, path, code in (("S1", "target/bucket.rs", BUCKET), ("S2", "target/offset.rs", OFFSET), ("S3", "target/split.rs", SPLIT)):
            self.assertTrue(self.integ.integrate(cid, self.cand(path, code)).accepted)
        self.events.emit("run.finished", actor="scheduler", payload={"accepted": self.integ.accepted, "exportable": True, "accepted_tree": self.integ.tree_hash()})
        code, out = self._cli("export", run_id="run", profile_dir=None)
        self.assertEqual(code, 0, out)
        export = self.run_dir / "export"
        self.assertIn("+    ts.div_euclid(width) * width", (export / "migration.patch").read_text())
        self.assertIn("ACCEPTED, exportable", (export / "report.md").read_text())
        _, status = self._cli("status", run_id="run")
        self.assertIn("3 of 3 kept", status)
        # A contract change after acceptance makes receipts stale: export must say so.
        d = self.ledger.record_decision(PROPOSAL, proposed_by="contract-steward", allowed_contracts=["time-arithmetic"])
        self.integ.invalidate(d["affected_chunks"], "contract changed")
        code, _ = self._cli("export", run_id="run", profile_dir=None)
        self.assertEqual(code, 1)
        self.assertIn("NOT EXPORTABLE", (export / "report.md").read_text())


if __name__ == "__main__":
    unittest.main()
