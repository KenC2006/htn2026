"""TypeScript to ArkTS from your own file: scan one .ts file, then build a project the team can migrate.

Same steps as newproject.py: scan (static, arkts/scan.cjs)  ->  settle each input's range  ->  RUN the original under Node to learn what
it really returns  ->  write the project folder. The device side (DevEco build, signed app, one launch per case on the HarmonyOS
emulator) is the prepared project's, copied as it is; only the pieces that name functions are generated here.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .paths import PROJECTS, ROOT
from .scan_python import Function, Param

PREPARED = ROOT / "fixtures" / "telemetry-workbench" / "ts-arkts-core"      # device harness and runners come from here
PARTS = Path(__file__).with_name("arkts")
DEVECO = Path(os.environ.get("PARITY_DEVECO_HOME", r"C:\Program Files\Huawei\DevEco Studio"))
KINDS = {"number": "int", "string": "str", "boolean": "bool", "number[]": "list[int]", "string[]": "list[str]", "boolean[]": "list[bool]"}
DEV_CASES, HIDDEN_CASES = 24, 40      # every case is one app launch on the emulator, about a second each


def is_ts(source: Path) -> bool:
    return Path(source).suffix.lower() == ".ts"


def scan(source: Path) -> list[Function]:
    node, compiler = DEVECO / "tools/node/node.exe", DEVECO / "tools/ohpm/node_modules/typescript/lib/typescript.js"
    if not node.is_file() or not compiler.is_file():
        raise SystemExit(f"reading TypeScript needs DevEco Studio ({DEVECO} not found; env/setup-arkts.md)")
    if Path(source).is_dir():
        raise SystemExit("TypeScript is migrated one file at a time: name the .ts file")
    ran = subprocess.run([str(node), str(PARTS / "scan.cjs"), str(compiler), str(source)], capture_output=True, text=True, encoding="utf-8")
    if ran.returncode != 0:
        raise SystemExit("cannot read that as TypeScript: " + (ran.stderr.strip().splitlines() or ["no output"])[-1][:200])
    found = []
    for f in json.loads(ran.stdout):
        one = Function(name=f["name"], module=Path(source).stem, file=Path(source), lineno=f["line"], source=f["source"], calls=f["calls"], needs=f["needs"],
                       returns_hint=f["returns"], reason=f["reason"],
                       params=[Param(p["name"], KINDS.get(p["type"]) if p["type"] else None) for p in f["params"]])
        one.ts_params = [(p["name"], p["type"] or p["said"]) for p in f["params"]]
        found.append(one)
    return found


def _stub(returns: str) -> str:
    return "[]" if returns.endswith("[]") else {"number": "0", "string": "''", "boolean": "false"}[returns]


def create(source: Path, name: str, chosen: list[str] | None = None, *, use_ai: bool = True, say=print) -> Path:
    from .newproject import _run_original, make_cases, rules_for, suggest
    source = Path(source).resolve()
    wanted = [f for f in scan(source) if f.ok and (chosen is None or f.name in chosen)]
    if not wanted:
        raise SystemExit("none of the chosen functions can be migrated")
    project = PROJECTS / name
    if project.exists():
        shutil.rmtree(project)
    for folder in ("legacy", "target", "chunks", "contracts", "view", "locked"):
        (project / folder).mkdir(parents=True)
    shutil.copy(source, project / "legacy" / source.name)
    shutil.copytree(PREPARED / "harness", project / "harness")
    (project / "runners").mkdir()
    for file in ("build.py", "signing.cjs"):
        shutil.copy(PREPARED / "runners" / file, project / "runners" / file)
    # the prepared runner, plus one thing: when the app dies instead of answering, say why (the device's own crash log)
    runner = (PREPARED / "runners" / "target.py").read_text(encoding="utf-8")
    silent = "raise RuntimeError('Missing fresh ArkTS completion; capture ended or device timed out')"
    names = "device_lock, read_cases, frames"
    assert silent in runner and names in runner
    (project / "runners" / "target.py").write_text(runner.replace(names, names + ", crash_report", 1).replace(
        silent, "raise RuntimeError('The app did not answer case '+case['case_id']+': it crashed or timed out. '+crash_report(target))"), encoding="utf-8", newline="\n")
    for file in ("source_runner.cjs", "policy.cjs"):
        shutil.copy(PARTS / file, project / "runners" / file)
    # the prepared project checks its own three result shapes; here any JSON value or any error message is a result
    (project / "runners" / "common.py").write_text((PREPARED / "runners" / "common.py").read_text(encoding="utf-8") + '''

def validate_observation(row, chunk):
    if row.get('status') == 'error':
        if not isinstance(row.get('error_code'), str) or 'value' in row:
            raise ValueError('malformed error observation')
    elif row.get('status') != 'ok' or 'value' not in row or 'error_code' in row:
        raise ValueError('crash or malformed observation')

def crash_report(target):
    try:
        newest = run([HDC,'-t',target,'shell','ls -t /data/log/faultlog/faultlogger/ | head -1']).strip()
        text = run([HDC,'-t',target,'shell','cat /data/log/faultlog/faultlogger/'+newest])
        lines = [s.strip() for s in text.splitlines() if s.startswith(('Reason:','Error message:','    at '))][:6]
        return 'Newest crash log on the device: '+' | '.join(lines)
    except Exception:
        return ''
''', encoding="utf-8", newline="\n")
    (project / "runners" / "source.py").write_text('''import argparse
from pathlib import Path
import tempfile
from common import NODE, TS, run, read_cases

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', required=True); parser.add_argument('--out', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cases = read_cases(args.cases)
    with tempfile.TemporaryDirectory(prefix='parity-ts-') as staging:
        run([NODE, root/'runners/source_runner.cjs', TS, root/'legacy', staging, Path(args.cases).resolve(), Path(args.out).resolve()])
    if [r['case_id'] for r in read_cases(args.out)] != [c['case_id'] for c in cases]:
        raise ValueError('oracle inventory mismatch')

if __name__ == '__main__':
    main()
''', encoding="utf-8", newline="\n")
    (project / "runners" / "functions.json").write_text(json.dumps(
        {"file": source.name, "functions": {f.name: {"params": [p.name for p in f.params]} for f in wanted}}, indent=2), encoding="utf-8", newline="\n")

    say(f"asking the model for realistic input ranges for {len(wanted)} functions…" if use_ai and os.environ.get("API_KEY") else "using default input ranges")
    hints = suggest(wanted) if use_ai else {}
    kept, skipped = [], {}
    for f in wanted:
        rules = rules_for(f, hints.get(f.name) if isinstance(hints.get(f.name), dict) else {})
        if isinstance(rules, str):
            skipped[f.name] = rules
            continue
        dev = make_cases(f.name, "?", rules, DEV_CASES, 1, (hints.get(f.name) or {}).get("examples") or [], "case")
        hidden = make_cases(f.name, "?", rules, HIDDEN_CASES, 9001, [], "hidden")
        first, again = _run_original(project, dev + hidden), _run_original(project, dev + hidden)
        ok = [o for o in first.values() if o["status"] == "ok"]
        if len(first) != len(dev + hidden) or any(o["status"] == "crash" for o in first.values()):
            skipped[f.name] = "the original did not run on the inputs I settled on"
        elif first != again:
            skipped[f.name] = "gave different answers for the same inputs when run twice"
        elif len(ok) < 0.5 * len(first):
            skipped[f.name] = "throws on most inputs in the range I settled on"
        else:
            kept.append({"f": f, "rules": rules, "dev": dev, "hidden": hidden, "seen": first,
                         "errors": sorted({o["error_code"] for o in first.values() if o["status"] == "error"})})
    # a function that calls a skipped one cannot import it
    names = {k["f"].name for k in kept}
    for k in [k for k in kept if any(c not in names for c in k["f"].calls)]:
        skipped[k["f"].name] = f"calls {', '.join(c for c in k['f'].calls if c not in names)}, which is not being migrated"
        kept.remove(k)
    if not kept:
        shutil.rmtree(project)
        raise SystemExit("no function survived being run:\n" + "\n".join(f"  {k}: {v}" for k, v in skipped.items()))
    order, by_name = [], {k["f"].name: k for k in kept}
    while len(order) < len(kept):                                   # what a piece calls comes first
        order.append(next((k for k in kept if k not in order and all(by_name[c] in order for c in k["f"].calls if c in by_name)), None)
                     or next(k for k in kept if k not in order))
    (project / "runners" / "functions.json").write_text(json.dumps(
        {"file": source.name, "functions": {k["f"].name: {"params": [p.name for p in k["f"].params]} for k in order}}, indent=2), encoding="utf-8", newline="\n")

    imports, arms = "", ""
    for i, k in enumerate(order):
        f = k["f"]
        k["id"] = f"T{i + 1}"
        signature = f"export function {f.name}({', '.join(f'{n}: {t}' for n, t in f.ts_params)}): {f.returns_hint}"
        (project / "target" / f"{f.name}.ets").write_text(f"{signature} {{ return {_stub(f.returns_hint)}; }}\n", encoding="utf-8", newline="\n")
        (project / "view" / f"{f.name}.ts").write_text("\n\n".join(f.needs + [f.source]) + "\n", encoding="utf-8", newline="\n")
        imports += f"import {{ {f.name} }} from '../core/{f.name}';\n"
        arms += f"      {'if' if not arms else 'else if'} (name === '{f.name}') o.value = {f.name}({', '.join(f'''input['{n}'] as {t}''' for n, t in f.ts_params)});\n"
        deps = [by_name[c]["id"] for c in f.calls if c in by_name]
        notes = (f"Write target/{f.name}.ets in ArkTS. Exact signature: `{signature}`. ArkTS is a strict subset of TypeScript and the compiler rejects, among others: "
                 "`any` and `unknown`, destructuring (`const [a, b] = x`, `[p, t] = [t, p]`, `{a, b} = o`), spread except of arrays into arrays or rest parameters, "
                 "object literals without a declared class or interface, `for..in`, function expressions (use arrow functions), indexing objects by string, "
                 "and the non-null `!` on things that can be written plainly. Use check_compile: the real DevEco compiler answers. "
                 "The code runs on the HarmonyOS engine, not on Node: something that compiles can still fail on the device, and the rejection then carries the device's crash log. "
                 "At the top of the file only declarations are allowed (functions, constants, classes, interfaces, types); no platform APIs, no console, no Date, no dynamic execution. ")
        if deps:
            notes += "Call the already migrated " + ", ".join(f"`{c}` with `import {{ {c} }} from './{c}'`" for c in f.calls if c in by_name) + " instead of re-implementing it. No other imports. "
        else:
            notes += "No imports. "
        if f.needs:
            notes += "The helpers and constants it relies on are shown with the source; port them into your file. "
        if k["errors"]:
            notes += f"Where the original throws, throw `new Error(<the same message>)` (seen so far: {', '.join(k['errors'])[:300]}). "
        shown = []
        for c in k["dev"]:
            o = k["seen"][c["case_id"]]
            result = json.dumps(o["value"], ensure_ascii=False) if o["status"] == "ok" else f"throws {o['error_code']}"
            if len(result) <= 300 and len(shown) < 8 and (len(shown) < 4 or result not in [r for _, r in shown]):
                shown.append((json.dumps(c["input"], ensure_ascii=False), result))
        notes += ("Text must match the original character for character, numbers exactly. Every input is inside the declared input range. "
                  "What the original really returns: " + "; ".join(f"{a} -> {r}" for a, r in shown) + ".")
        for c in k["dev"] + k["hidden"]:
            c["chunk_id"] = k["id"]
        (project / "chunks" / f"{k['id']}.json").write_text(json.dumps({
            "schema_version": 1, "chunk_id": k["id"], "profile": name, "source_files": [f"view/{f.name}.ts"], "exports": [f.name],
            "write_allowlist": [f"target/{f.name}.ets"], "depends_on": deps, "worker_notes": notes, "contract_ids": [f"behavior-{f.name}"],
            "limits": {"attempts": 3, "verify_seconds_per_attempt": 300}, "example_input": k["dev"][0]["input"], "input_domain": k["rules"],
            "origin": {"module": f.module, "file": source.name, "line": f.lineno}}, indent=2) + "\n", encoding="utf-8", newline="\n")
        (project / "contracts" / f"behavior-{f.name}.json").write_text(json.dumps({
            "schema_version": 1, "contract_id": f"behavior-{f.name}", "version": 1,
            "behavior": f"For every input inside the declared input range, the ArkTS `{f.name}` returns exactly what the TypeScript original returns: "
                        "the same numbers, the same text character for character, and throws an Error with the same message where the original throws.",
            "guidance": []}, indent=2) + "\n", encoding="utf-8", newline="\n")

    ability = project / "harness/entry/src/main/ets/entryability/EntryAbility.ets"
    ability.write_text((PARTS / "EntryAbility.ets").read_text(encoding="utf-8").replace("//IMPORTS\n", imports).replace("      //ARMS\n", arms), encoding="utf-8", newline="\n")
    (project / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for k in order for c in k["dev"]), encoding="utf-8", newline="\n")
    (project / "locked" / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for k in order for c in k["hidden"]), encoding="utf-8", newline="\n")
    prepared = json.loads((PREPARED / "profile.json").read_text(encoding="utf-8"))
    (project / "profile.json").write_text(json.dumps({
        **prepared, "profile": name,
        "frozen": ["profile.json", "chunks/*", "contracts/*", "legacy/*", "view/*", "runners/*", "harness/*", "cases.jsonl", "locked/*"],
        "oracle_paths": ["legacy", "view", "cases.jsonl", "locked"], "skipped": skipped}, indent=2) + "\n", encoding="utf-8", newline="\n")
    say(f"project ready: {project}   {len(order)} pieces, {sum(len(k['dev']) for k in order)} test inputs, {sum(len(k['hidden']) for k in order)} hidden")
    for fname, why in skipped.items():
        say(f"  skipped {fname}: {why}")
    return project
