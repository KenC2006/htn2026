"""`parity new file.ts`: scanning real TypeScript and building a project from it. No model calls
(use_ai=False), no DevEco build (verify_build=False): host-only, like test_new.py's Rust route.
A full device proof (real signed build, install, and differential check against a hand-written
ArkTS candidate on the emulator) was run manually; see docs/TS_ONBOARDING_PLAN.md."""
import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from parity import newproject_ts
from parity.engine.domain import in_domain
from parity.scan_typescript import node_and_ts, scan

NODE, TS = node_and_ts()
HAVE_TOOLCHAIN = NODE.is_file() and TS.is_file()

SOURCE = textwrap.dedent("""
    export function clamp(value: number, lower: number, upper: number): number {
      if (lower > upper) throw new Error("INVALID_BOUNDS");
      return Math.min(upper, Math.max(lower, value));
    }

    export function clampAll(values: number[], lower: number, upper: number): number[] {
      const out: number[] = [];
      for (let i = 0; i < values.length; i++) {
        if (lower > upper) throw new Error("INVALID_BOUNDS");
        out.push(clamp(values[i], lower, upper));
      }
      return out;
    }

    export function shout(text: string): string {
      return text.toUpperCase();
    }

    export function stamped(text: string): string {
      return `${Date.now()} ${text}`;
    }
""")


@unittest.skipUnless(HAVE_TOOLCHAIN, "DevEco Studio (Node + TypeScript) not found; set PARITY_NODE/PARITY_TYPESCRIPT")
class NewProjectTSTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="parity-new-ts-"))
        self.file = self.tmp / "words.ts"
        self.file.write_text(SOURCE, encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(shutil.rmtree, newproject_ts.PROJECTS / "selftest-ts-words", ignore_errors=True)

    def test_scan_keeps_pure_functions_and_says_why_the_others_cannot_be_migrated(self):
        found = {f.name: f for f in scan(self.file)}
        self.assertTrue(found["clamp"].ok and found["clampAll"].ok and found["shout"].ok)
        self.assertEqual(found["clampAll"].calls, ["clamp"])
        self.assertIn("Date", found["stamped"].reason)

    def test_project_is_built_from_what_the_original_really_does(self):
        project = newproject_ts.create_ts(self.file, "selftest-ts-words", ["clamp", "clampAll", "shout"], use_ai=False, verify_build=False, say=lambda m: None)
        chunks = {json.loads(p.read_text())["exports"][0]: json.loads(p.read_text()) for p in (project / "chunks").glob("*.json")}
        # `clamp` on its own raises on ~half of a wide independent [lower, upper] range and gets
        # skipped, the same way newproject.py skips a Python function that mostly raises; clampAll
        # (which only checks bounds per element, so an empty list never raises) survives.
        self.assertIn("clampAll", chunks)
        self.assertIn("shout", chunks)

        dispatch = json.loads((project / "runners" / "dispatch.json").read_text())
        self.assertEqual(dispatch["module"], "words")
        self.assertEqual(set(dispatch["functions"]), {"clampAll", "shout"})
        self.assertEqual(dispatch["functions"]["shout"]["returns"], "string")
        self.assertEqual(dispatch["functions"]["clampAll"]["returns"], "number[]")
        self.assertEqual(dispatch["functions"]["clampAll"]["errors"], ["INVALID_BOUNDS"])

        placeholder = (project / "target" / "shout.ets").read_text()
        self.assertIn("export function shout(text: string): string", placeholder)

        cases = [json.loads(l) for l in (project / "cases.jsonl").read_text().splitlines()]
        hidden = [json.loads(l) for l in (project / "locked" / "cases.jsonl").read_text().splitlines()]
        self.assertEqual(len(cases), 2 * newproject_ts.DEV_CASES)
        self.assertFalse({json.dumps(c["input"], sort_keys=True) for c in cases if c["export"] == "shout"} >=
                         {json.dumps(c["input"], sort_keys=True) for c in hidden if c["export"] == "shout"}, "hidden inputs must not just repeat the visible ones")
        for c in cases:
            for key, value in c["input"].items():
                self.assertEqual(in_domain(chunks[c["export"]]["input_domain"][key], value), "")

        entry_ability = (project / "harness" / "entry" / "src" / "main" / "ets" / "entryability" / "EntryAbility.ets").read_text()
        self.assertIn("import { shout } from '../core/shout';", entry_ability)
        self.assertIn("c.export === 'shout'", entry_ability)

        # a candidate that parses cleanly and is well-formed under the generated, generic policy.cjs
        import subprocess
        node, ts = node_and_ts()
        good = "export function shout(text: string): string {\n  return text.toUpperCase();\n}\n"
        (project / "target" / "shout.ets").write_text(good, encoding="utf-8")
        ran = subprocess.run([str(node), str(project / "runners" / "policy.cjs"), str(ts), str(project / "target")], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
