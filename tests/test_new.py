"""`parity new`: scanning real Python and building a project from it. No model calls (use_ai=False)."""
import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from parity import newproject
from parity.engine import gate
from parity.engine.domain import in_domain
from parity.scan_python import scan

SOURCE = textwrap.dedent('''
    import os
    import time

    SUFFIXES = ["th", "st", "nd", "rd"]
    _seen = []


    def ordinal(n: int) -> str:
        """ordinal(1) == "1st" """
        return f"{n}{_suffix(n)}"


    def _suffix(n: int) -> str:
        n = abs(n)
        return "th" if 10 <= n % 100 <= 20 else SUFFIXES[n % 10] if n % 10 < 4 else "th"


    def halves(total: int, parts: int = 2) -> list[int]:
        if parts <= 0:
            raise ValueError("parts must be positive")
        return [total // parts] * parts


    def stamp(text: str) -> str:
        return f"{time.time()} {text}"


    def home(name: str) -> str:
        return os.path.join(os.path.expanduser("~"), name)


    def remember(x: int) -> int:
        _seen.append(x)
        return len(_seen)


    def shout(*words):
        return " ".join(words).upper()
''')


class NewProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="parity-new-"))
        self.file = self.tmp / "words.py"
        self.file.write_text(SOURCE, encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(shutil.rmtree, newproject.ROOT / "projects" / "selftest-words", ignore_errors=True)

    def test_scan_keeps_pure_functions_and_says_why_the_others_cannot_be_migrated(self):
        found = {f.name: f for f in scan(self.file)}
        self.assertTrue(found["ordinal"].ok and found["halves"].ok and found["_suffix"].ok)
        self.assertEqual(found["ordinal"].calls, ["_suffix"])
        self.assertTrue(any("SUFFIXES" in s for s in found["ordinal"].needs), "the table a helper uses must travel with the function")
        self.assertIn("time", found["stamp"].reason)
        self.assertIn("os", found["home"].reason)
        self.assertIn("_seen", found["remember"].reason)
        self.assertIn("*args", found["shout"].reason)

    def test_project_is_built_from_what_the_original_really_does(self):
        project = newproject.create(self.file, "selftest-words", ["ordinal", "halves"], use_ai=False, say=lambda m: None)
        chunks = {json.loads(p.read_text())["exports"][0]: json.loads(p.read_text()) for p in (project / "chunks").glob("*.json")}
        self.assertIn("-> String", chunks["ordinal"]["worker_notes"])
        self.assertIn("Result<Vec<i64>, String>", chunks["halves"]["worker_notes"], "the original raises ValueError in range, so the Rust side must be able to")
        self.assertIn("_suffix", chunks["ordinal"]["worker_notes"])           # private helper is ported inside the piece
        self.assertIn("def _suffix", (project / "view" / "ordinal.py").read_text())
        cases = [json.loads(l) for l in (project / "cases.jsonl").read_text().splitlines()]
        hidden = [json.loads(l) for l in (project / "locked" / "cases.jsonl").read_text().splitlines()]
        self.assertEqual(len(cases), 2 * newproject.DEV_CASES)
        self.assertFalse({json.dumps(c["input"], sort_keys=True) for c in cases if c["export"] == "ordinal"} >=
                         {json.dumps(c["input"], sort_keys=True) for c in hidden if c["export"] == "ordinal"}, "hidden inputs must not just repeat the visible ones")
        for c in cases:
            for key, value in c["input"].items():
                self.assertEqual(in_domain(chunks[c["export"]]["input_domain"][key], value), "")

        # the scaffold compiles, and its placeholders are rejected by the checker (wrong output), not by a build error
        run_dir = self.tmp / "run"
        gate.freeze(project)
        piece = chunks["ordinal"]
        placeholder = {"files": [{"path": p, "content": (project / p).read_text()} for p in piece["write_allowlist"]]}
        verdict = gate.check(project, piece["chunk_id"], placeholder, project / "cases.jsonl", run_dir, attempt_id="placeholder")
        self.assertEqual(verdict.status, "REJECTED_BEHAVIOR", verdict.detail)

        # a correct hand-written port passes, text and all
        good = ('pub fn ordinal(n: i64) -> String {\n    let a = n.abs();\n    let s = if (10..=20).contains(&(a % 100)) { "th" } else { match a % 10 { 1 => "st", 2 => "nd", 3 => "rd", _ => "th" } };\n'
                '    format!("{}{}", n, s)\n}\n')
        verdict = gate.check(project, piece["chunk_id"], {"files": [{"path": piece["write_allowlist"][0], "content": good}]},
                             project / "cases.jsonl", run_dir, attempt_id="good")
        self.assertEqual(verdict.status, "ACCEPTED", verdict.detail)


if __name__ == "__main__":
    unittest.main()
