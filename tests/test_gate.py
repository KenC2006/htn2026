"""Gate self-checks (plan section 11, "The verifier's own checks"). Uses the real rustc.

    python -m unittest tests.test_gate -v
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ratchet.engine import gate
from ratchet.engine.events import EventLog

FIXTURE = Path(__file__).parent / "gate_fixture"
GOOD = "pub fn bucket(ts: i64, width: i64) -> i64 {\n    ts.div_euclid(width) * width\n}\n"
# Verbatim from the first real worker run (workflows/smoke.py): truncates toward zero.
WORKER_A = "pub fn bucket(ts: i64, width: i64) -> i64 {\n    assert!(width > 0, \"width must be positive\");\n    (ts / width) * width\n}\n"


def cand(content=GOOD, path="target/bucket.rs", **extra):
    return {"files": [{"path": path, "content": content}], **extra}


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ratchet-gate-"))
        self.profile = self.tmp / "profile"
        shutil.copytree(FIXTURE, self.profile)
        gate.freeze(self.profile)
        self.run_dir = self.tmp / "run"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, candidate, **kw):
        return gate.check(self.profile, "S1", candidate, self.profile / "cases.jsonl", self.run_dir, **kw)

    def test_known_good_is_accepted(self):
        v = self.check(cand(), events=EventLog(self.run_dir, "t"))
        self.assertEqual(v.status, "ACCEPTED", v.detail)
        self.assertEqual(v.cases, {"expected": 6, "observed": 6, "passed": 6, "skipped": 0})
        types = [json.loads(l)["type"] for l in (self.run_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(types, ["candidate.submitted", "candidate.verified"])

    def test_real_worker_bug_is_rejected_with_counterexample(self):
        v = self.check(cand(WORKER_A))
        self.assertEqual(v.status, "REJECTED_BEHAVIOR")
        self.assertEqual(v.counterexample["case_id"], "negative-boundary-01")
        self.assertEqual(v.counterexample["source"]["value"], -1000)
        self.assertEqual(v.counterexample["target"]["value"], 0)
        self.assertEqual(v.mismatched_case_ids, ["negative-boundary-01", "large-negative"])

    def test_constant_stub_is_rejected(self):
        v = self.check(cand("pub fn bucket(_ts: i64, _width: i64) -> i64 { 0 }\n"))
        self.assertEqual(v.status, "REJECTED_BEHAVIOR")

    def test_panic_counts_as_failure(self):
        v = self.check(cand("pub fn bucket(_ts: i64, _width: i64) -> i64 { panic!(\"no\") }\n"))
        self.assertEqual(v.status, "REJECTED_BEHAVIOR")
        self.assertEqual(v.counterexample["target"]["status"], "crash")

    def test_edit_outside_allowlist_is_rejected(self):
        for path in ("runners/target.py", "cases.jsonl", "harness/main.rs"):
            self.assertEqual(self.check(cand(path=path)).status, "REJECTED_POLICY", path)

    def test_path_traversal_is_rejected(self):
        for path in ("../escape.rs", "target/../../x.rs", "/abs.rs", "C:/x.rs", "target\\bucket.rs"):
            self.assertEqual(self.check(cand(path=path)).status, "REJECTED_POLICY", path)

    def test_unsafe_and_stub_macros_are_rejected(self):
        self.assertEqual(self.check(cand("pub fn bucket(a: i64, b: i64) -> i64 { unsafe { a / b * b } }")).status, "REJECTED_POLICY")
        self.assertEqual(self.check(cand("pub fn bucket(_a: i64, _b: i64) -> i64 { todo!() }")).status, "REJECTED_POLICY")

    def test_compile_error_is_rejected(self):
        self.assertEqual(self.check(cand("pub fn bucket(ts: i64) -> i64 { ts }")).status, "REJECTED_BUILD")

    def test_candidate_cannot_reach_the_original_source(self):
        v = self.check(cand('pub const SRC: &str = include_str!("../legacy/bucket.py");\n' + GOOD))
        self.assertEqual(v.status, "REJECTED_BUILD")
        self.assertFalse((self.run_dir / "candidates" / "S1-1" / "legacy").exists())

    def test_stale_contract_is_rejected(self):
        v = self.check(cand(contract_hashes={"bucket-semantics": "v1"}), current_contract_hashes={"bucket-semantics": "v2"})
        self.assertEqual(v.status, "STALE")
        v = self.check(cand(contract_hashes={"bucket-semantics": "v2"}), current_contract_hashes={"bucket-semantics": "v2"})
        self.assertEqual(v.status, "ACCEPTED", v.detail)

    def test_fixture_changed_after_freeze_is_rejected(self):
        with (self.profile / "cases.jsonl").open("a") as f:
            f.write("\n")
        self.assertEqual(self.check(cand()).status, "REJECTED_INTEGRITY")

    def test_repeat_checks_do_not_trip_integrity(self):
        # Regression: the source runner's __pycache__ once looked like fixture tampering.
        self.assertEqual(self.check(cand(WORKER_A), attempt_id="S1:1").status, "REJECTED_BEHAVIOR")
        self.assertEqual(self.check(cand(), attempt_id="S1:2").status, "ACCEPTED")

    def test_missing_observation_is_rejected(self):
        runner = self.profile / "runners" / "target.py"
        runner.write_text(runner.read_text().replace(
            "c = json.loads(line); t", "c = json.loads(line)\n        if c['case_id'] == 'zero': continue\n        t"))
        gate.freeze(self.profile)
        v = self.check(cand())
        self.assertEqual(v.status, "REJECTED_TEST")
        self.assertIn("zero", v.detail)

    def test_credentials_are_not_visible_to_candidate_processes(self):
        os.environ["API_KEY"] = "sk-test"
        try:
            env = gate._scrubbed_env()
        finally:
            del os.environ["API_KEY"]
        self.assertNotIn("API_KEY", env)
        self.assertIn("PATH", {k.upper() for k in env})


if __name__ == "__main__":
    unittest.main()
