"""The TypeScript scanner: which functions of a .ts file can be migrated, and why not. Needs DevEco's Node and TypeScript; no emulator."""
import tempfile
import unittest
from pathlib import Path

from parity import newarkts

SOURCE = '''
const LIMIT = 3;
function helper(word: string): string { return word.slice(0, LIMIT); }
export function short(word: string): string { return helper(word); }
export function twice(word: string, times: number): string { return short(word).repeat(times); }
export function total(values: number[]): number { let s = 0; for (const v of values) s += v; return s; }
export function today(label: string): string { return label + new Date().toISOString(); }
export function pick<T>(items: T[]): T { return items[0]; }
export function options(text: string, settings?: { long: boolean }): string { return text; }
export async function later(text: string): Promise<string> { return text; }
'''


@unittest.skipUnless((newarkts.DEVECO / "tools/node/node.exe").is_file(), "DevEco Studio is not installed")
class ScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "sample.ts"
            file.write_text(SOURCE, encoding="utf-8")
            cls.found = {f.name: f for f in newarkts.scan(file)}

    def test_only_exported_functions_are_listed(self):
        self.assertNotIn("helper", self.found)
        self.assertEqual(set(self.found), {"short", "twice", "total", "today", "pick", "options", "later"})

    def test_supported_functions_with_types_helpers_and_calls(self):
        self.assertTrue(all(self.found[n].ok for n in ("short", "twice", "total")))
        self.assertEqual([(p.name, p.type) for p in self.found["twice"].params], [("word", "str"), ("times", "int")])
        self.assertEqual(self.found["total"].params[0].type, "list[int]")
        self.assertEqual(self.found["twice"].calls, ["short"])                     # an exported function it calls is a separate piece
        self.assertEqual(len(self.found["short"].needs), 2)                        # the helper and the constant the helper uses

    def test_refusals_say_why(self):
        self.assertIn("Date", self.found["today"].reason)
        self.assertIn("generic", self.found["pick"].reason)
        self.assertIn("optional", self.found["options"].reason)
        self.assertIn("async", self.found["later"].reason)


if __name__ == "__main__":
    unittest.main()
