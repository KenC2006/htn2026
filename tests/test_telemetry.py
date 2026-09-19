"""The telemetry-workbench example (Aidan's fixture in Parity's folder format). No model calls."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from parity.engine import gate
from parity.engine.domain import in_domain

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "telemetry-workbench" / "py-rust-batch"
KNOWN_GOOD = ROOT / "tests" / "telemetry_known_good"


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class TelemetryWorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp(prefix="parity-tw-"))
        self.addCleanup(shutil.rmtree, self.run_dir, ignore_errors=True)
        self.chunks = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (PROJECT / "chunks").glob("*.json")}

    def test_every_case_is_inside_the_declared_input_range(self):
        for case in lines(PROJECT / "cases.jsonl") + lines(PROJECT / "locked" / "cases.jsonl"):
            for key, value in case["input"].items():
                self.assertEqual(in_domain(self.chunks[case["chunk_id"]]["input_domain"][key], value), "", case["case_id"])

    def test_out_of_range_records_are_refused(self):
        rule = self.chunks["P2"]["input_domain"]["records"]
        self.assertTrue(in_domain(rule, [["", 0, 0, 0]]))             # empty sensor id
        self.assertTrue(in_domain(rule, [["s1", 0, -1, 0]]))          # negative sequence
        self.assertTrue(in_domain(rule, [["s1", 0, 0]]))              # wrong arity
        self.assertTrue(in_domain(rule, [["s 1", 0, 0, 0]]))          # space is not in the sensor alphabet

    def test_placeholder_is_rejected_and_the_reference_rust_is_accepted(self):
        piece = self.chunks["P2"]
        path = piece["write_allowlist"][0]
        stub = {"files": [{"path": path, "content": (PROJECT / path).read_text(encoding="utf-8")}]}
        verdict = gate.check(PROJECT, "P2", stub, PROJECT / "cases.jsonl", self.run_dir, attempt_id="stub")
        self.assertEqual(verdict.status, "REJECTED_BEHAVIOR", verdict.detail)
        good = {"files": [{"path": path, "content": (KNOWN_GOOD / path).read_text(encoding="utf-8")}]}
        verdict = gate.check(PROJECT, "P2", good, PROJECT / "cases.jsonl", self.run_dir, attempt_id="good")
        self.assertEqual(verdict.status, "ACCEPTED", verdict.detail)

    def test_truncating_division_is_caught_by_a_named_case(self):
        piece = self.chunks["P2"]
        path = piece["write_allowlist"][0]
        wrong = (KNOWN_GOOD / path).read_text(encoding="utf-8").replace("timestamp_ms.div_euclid(width_ms)", "(timestamp_ms / width_ms)")
        verdict = gate.check(PROJECT, "P2", {"files": [{"path": path, "content": wrong}]}, PROJECT / "cases.jsonl", self.run_dir, attempt_id="trunc")
        self.assertEqual(verdict.status, "REJECTED_BEHAVIOR", verdict.detail)
        self.assertTrue(verdict.counterexample["case_id"].startswith("P2-"), verdict.counterexample)


if __name__ == "__main__":
    unittest.main()
