"""Ledger + integrator checks, no model calls. Uses the real rustc.

    python -m unittest tests.test_flow -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ratchet.engine import gate
from ratchet.engine.contracts import ContractLedger, DecisionRejected
from ratchet.engine.events import EventLog
from ratchet.engine.integrator import Integrator

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


if __name__ == "__main__":
    unittest.main()
