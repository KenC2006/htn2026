"""Ledger + integrator checks, no model calls. Uses the real rustc.

    python -m unittest tests.test_flow -v
"""
import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ratchet.engine import gate
from ratchet.engine.contracts import ContractLedger, DecisionRejected
from ratchet.engine.events import EventLog
from ratchet.engine.integrator import Integrator
from ratchet.engine.tools import RunContext
from ratchet.framework.team import TEAM

FIXTURE = Path(__file__).parent / "flow_fixture"
BUCKET = "pub fn bucket(ts: i64, width: i64) -> i64 {\n    ts.div_euclid(width) * width\n}\n"
OFFSET = "pub fn offset(ts: i64, width: i64) -> i64 {\n    ts.rem_euclid(width)\n}\n"
SPLIT = "pub fn split(ts: i64, width: i64) -> (i64, i64) {\n    (crate::bucket::bucket(ts, width), crate::offset::offset(ts, width))\n}\n"
BAD_BUCKET = "pub fn bucket(ts: i64, width: i64) -> i64 {\n    ts / width * width\n}\n"
PROPOSAL = {"contract_id": "time-arithmetic", "kind": "implementation_clarification", "question": "negative ts?",
            "ruling": "Use div_euclid / rem_euclid.", "evidence_refs": ["legacy/timeparts.py", "bucket-neg-one"]}


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ratchet-flow-"))
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


if __name__ == "__main__":
    unittest.main()
