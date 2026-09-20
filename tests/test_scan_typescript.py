"""Scanning real TypeScript: which top-level `function`s can be migrated, and why the rest can't.
Host-only: needs Node + the TypeScript compiler DevEco ships (see scan_typescript.node_and_ts),
not the emulator. Skips itself if neither is present, matching `parity doctor`'s treatment of the
ArkTS toolchain as optional."""
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from parity.scan_typescript import node_and_ts, scan

NODE, TS = node_and_ts()
HAVE_TOOLCHAIN = NODE.is_file() and TS.is_file()

SOURCE = textwrap.dedent("""
    export function clamp(value: number, lower: number, upper: number): number {
      if (lower > upper) throw new Error("INVALID_BOUNDS");
      return Math.min(upper, Math.max(lower, value));
    }

    export function clampAll(values: number[], lower: number, upper: number): number[] {
      return values.map((v) => clamp(v, lower, upper));
    }

    export function shout(text: string): string {
      return text.toUpperCase();
    }

    export function stamped(text: string): string {
      return `${Date.now()} ${text}`;
    }

    export function withDefault(a: number, b: number = 5): number {
      return a + b;
    }

    let counter = 0;
    export function remember(x: number): number {
      counter += x;
      return counter;
    }

    export async function fetchIt(url: string): Promise<string> {
      return url;
    }

    export function noInputs(): number {
      return 1;
    }

    export function untyped(a): number {
      return a;
    }
""")


@unittest.skipUnless(HAVE_TOOLCHAIN, "DevEco Studio (Node + TypeScript) not found; set PARITY_NODE/PARITY_TYPESCRIPT")
class ScanTypeScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="parity-scan-ts-"))
        self.file = self.tmp / "sample.ts"
        self.file.write_text(SOURCE, encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.found = {f.name: f for f in scan(self.file)}

    def test_pure_functions_qualify(self):
        self.assertTrue(self.found["clamp"].ok, self.found["clamp"].reason)
        self.assertTrue(self.found["clampAll"].ok, self.found["clampAll"].reason)
        self.assertTrue(self.found["shout"].ok, self.found["shout"].reason)
        self.assertTrue(self.found["withDefault"].ok, self.found["withDefault"].reason)

    def test_calls_and_needs_follow_the_closure(self):
        self.assertEqual(self.found["clampAll"].calls, ["clamp"])
        self.assertTrue(any("clamp" in s for s in self.found["clampAll"].needs), "the callee's source must travel with the caller")

    def test_types_map_to_domain_vocabulary(self):
        by_name = {p.name: p.type for p in self.found["clampAll"].params}
        self.assertEqual(by_name, {"values": "list[float]", "lower": "float", "upper": "float"})
        self.assertEqual([p.type for p in self.found["shout"].params], ["str"])

    def test_default_value_is_reported(self):
        p = {p.name: p for p in self.found["withDefault"].params}["b"]
        self.assertTrue(p.has_default)
        self.assertEqual(self.found["withDefault"].defaults["b"], 5)

    def test_impure_functions_are_rejected_with_a_reason(self):
        self.assertIn("Date", self.found["stamped"].reason)
        self.assertIn("counter", self.found["remember"].reason)
        self.assertIn("async", self.found["fetchIt"].reason)
        self.assertIn("no inputs", self.found["noInputs"].reason)

    def test_missing_type_hint_is_reported_as_no_type_not_a_hard_failure(self):
        # mirrors scan_python.py: an unsupported/missing annotation is surfaced as `type=None` and
        # left for newproject.rules_for to try an AI suggestion on, not rejected at scan time.
        fn = self.found["untyped"]
        self.assertIsNone(fn.params[0].type)


if __name__ == "__main__":
    unittest.main()
