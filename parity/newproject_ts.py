"""`parity new <file.ts>`: turn a real, single-file TypeScript module into an ArkTS migration project.

Same pipeline as newproject.py's Python -> Rust route, and reuses its language-agnostic pieces
(`rules_for`, `default_rule`, `make_value`, `make_cases`, `suggest`) unchanged: scan
(scan_typescript.py, static) -> settle each input's type and range -> RUN the real TypeScript file
under Node to learn what it actually returns and raises -> write the project folder (same format
CONTRACTS.md and the gate already expect). See docs/TS_ONBOARDING_PLAN.md for the design.

Nothing here is trusted from an AI suggestion either: every suggested range is tried on the real
file, and the expected outputs always come from running it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import newproject, scan_typescript, templates_arkts
from .paths import PROJECTS

DEV_CASES, HIDDEN_CASES = newproject.DEV_CASES, newproject.HIDDEN_CASES
BIGGEST_OUTPUT = newproject.BIGGEST_OUTPUT

ARKTS_SCALAR = {"int": "number", "float": "number", "str": "string", "bool": "boolean"}


def arkts_type(kind: str) -> str:
    if kind.startswith("list["):
        return arkts_type(kind[5:-1]) + "[]"
    return ARKTS_SCALAR[kind]


def param_type(rule: dict) -> str:
    t = arkts_type(rule["type"])
    return f"{t} | null" if rule.get("nullable") else t


# ── what the original really returns, ArkTS-flavored (no int/float split; TS `number` is one type) ──
def shape_of_ts(values: list) -> str | None:
    seen = [v for v in values if v is not None]
    if len(seen) != len(values) or not seen:
        return None   # null/undefined outputs are not supported yet (docs/TS_ONBOARDING_PLAN.md)
    if all(isinstance(v, bool) for v in seen):
        return "boolean"
    if all(isinstance(v, str) for v in seen):
        return "string"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seen):
        return "number"
    if all(isinstance(v, list) for v in seen):
        inner = shape_of_ts([x for v in seen for x in v])
        return f"{inner}[]" if inner else None
    return None


def _probe_run(project: Path, cases: list[dict]) -> dict:
    """Runs source_runner.cjs directly (not runners/source.py) so probing works before dispatch.json's
    returns/errors are known: source_runner.cjs only ever needs `module` and each function's `params`."""
    node, ts = scan_typescript.node_and_ts()
    tmp = project / "_probe"
    tmp.mkdir(exist_ok=True)
    cases_path, out_path, out_dir = tmp / "cases.jsonl", tmp / "obs.jsonl", tmp / "out"
    cases_path.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    ran = subprocess.run([str(node), str(project / "runners" / "source_runner.cjs"), str(ts), str(project / "legacy"),
                          str(out_dir), str(cases_path), str(out_path)], cwd=project, capture_output=True, text=True, timeout=180)
    if ran.returncode != 0:
        print(f"  (the original could not be run: {ran.stderr.strip().splitlines()[-1][:200] if ran.stderr.strip() else 'no error text'})")
    obs = {}
    if out_path.exists():
        obs = {o["case_id"]: o for o in map(json.loads, out_path.read_text(encoding="utf-8").splitlines())}
    shutil.rmtree(tmp, ignore_errors=True)
    return obs


def create_ts(source: Path, name: str, chosen: list[str] | None = None, *, use_ai: bool = True, verify_build: bool = True, say=print) -> Path:
    """Build projects/<name>/ from a single .ts file. Returns the project folder."""
    source = Path(source).resolve()
    if not source.is_file():
        raise SystemExit("only a single .ts file is supported for now (docs/TS_ONBOARDING_PLAN.md)")
    functions = scan_typescript.scan(source)
    wanted = [f for f in functions if f.ok and (chosen is None and not f.name.startswith("_") or chosen is not None and f.name in chosen)]
    if not wanted:
        raise SystemExit("none of the chosen functions can be migrated")
    names = [f.name for f in wanted]
    if len(set(names)) != len(names):
        raise SystemExit(f"two chosen functions share a name ({sorted(n for n in names if names.count(n) > 1)[0]})")

    project = PROJECTS / name
    if project.exists():
        shutil.rmtree(project)
    for folder in ("legacy", "runners", "harness", "target", "chunks", "contracts", "view", "locked"):
        (project / folder).mkdir(parents=True)

    shutil.copy(source, project / "legacy" / source.name)
    module = source.stem

    (project / "runners" / "common.py").write_text(templates_arkts.COMMON_PY, encoding="utf-8", newline="\n")
    (project / "runners" / "build.py").write_text(templates_arkts.BUILD_PY, encoding="utf-8", newline="\n")
    (project / "runners" / "target.py").write_text(templates_arkts.TARGET_PY, encoding="utf-8", newline="\n")
    (project / "runners" / "source.py").write_text(templates_arkts.SOURCE_PY, encoding="utf-8", newline="\n")
    (project / "runners" / "signing.cjs").write_text(templates_arkts.SIGNING_CJS, encoding="utf-8", newline="\n")
    (project / "runners" / "source_runner.cjs").write_text(templates_arkts.SOURCE_RUNNER_CJS, encoding="utf-8", newline="\n")
    (project / "runners" / "policy.cjs").write_text(templates_arkts.POLICY_CJS, encoding="utf-8", newline="\n")

    # preliminary dispatch.json: only what source_runner.cjs needs to run the real file at all
    # (module + per-function params). `returns`/`errors` are learned below, from running it.
    prelim = {"module": module, "functions": {f.name: {"file": f"{f.name}.ets",
              "params": [{"name": p.name, "type": None} for p in f.params], "returns": "number", "errors": [], "allowedImports": []} for f in wanted}}
    (project / "runners" / "dispatch.json").write_text(json.dumps(prelim, indent=2) + "\n", encoding="utf-8", newline="\n")

    say(f"asking the model for realistic input ranges for {len(wanted)} functions…" if use_ai and os.environ.get("API_KEY") else "using default input ranges")
    hints = newproject.suggest(wanted) if use_ai else {}

    kept, skipped = [], {}
    for f in wanted:
        hint = hints.get(f.name) if isinstance(hints.get(f.name), dict) else {}
        rules = newproject.rules_for(f, hint)
        if isinstance(rules, str):
            skipped[f.name] = rules
            continue
        dev = newproject.make_cases(f.name, "?", rules, DEV_CASES, 1, hint.get("examples") or [], "case")
        hidden = newproject.make_cases(f.name, "?", rules, HIDDEN_CASES, 9001, [], "hidden")
        first, again = _probe_run(project, dev + hidden), _probe_run(project, dev + hidden)
        strip = lambda o: (o.get("status"), json.dumps(o.get("value")), o.get("error_code"))  # noqa: E731
        ok = [o for o in first.values() if o["status"] == "ok"]
        crashes = [o for o in first.values() if o["status"] == "crash"]
        errors = sorted({o["error_code"] for o in first.values() if o["status"] == "error"})
        if len(first) != len(dev + hidden) or crashes:
            skipped[f.name] = f"returns something I cannot carry across ({(crashes[0]['diagnostics'] if crashes else 'the original did not run')[:90]})"
        elif {k: strip(v) for k, v in first.items()} != {k: strip(v) for k, v in again.items()}:
            skipped[f.name] = "gave different answers for the same inputs when run twice"
        elif any(len(json.dumps(o.get("value"))) > BIGGEST_OUTPUT for o in first.values()):
            skipped[f.name] = f"returns outputs over {BIGGEST_OUTPUT} characters for some inputs in the range I settled on"
        elif len(ok) < 0.5 * len(first):
            skipped[f.name] = f"raises {errors[0] if errors else 'errors'} on most inputs in the range I settled on"
        else:
            shape = shape_of_ts([o["value"] for o in ok])
            if shape is None:
                skipped[f.name] = "returns null/undefined or mixed-shape values for some inputs, which ArkTS cannot hold in one field yet"
            else:
                kept.append({"f": f, "rules": rules, "returns": shape, "errors": errors, "dev": dev, "hidden": hidden, "seen": first})
    if not kept:
        shutil.rmtree(project)
        raise SystemExit("no function survived being run:\n" + "\n".join(f"  {k}: {v}" for k, v in skipped.items()))

    # order: what a piece calls comes first (same pass newproject.create does for Rust)
    order, by_name = [], {k["f"].name: k for k in kept}
    while len(order) < len(kept):
        for k in kept:
            if k not in order and all(c not in by_name or by_name[c] in order for c in k["f"].calls):
                order.append(k)
                break
        else:
            order += [k for k in kept if k not in order]
    for i, k in enumerate(order):
        k["id"] = f"F{i + 1}"

    dispatch_functions = {}
    for k in order:
        f, rules = k["f"], k["rules"]
        params = [{"name": p.name, "type": param_type(rules[p.name])} for p in f.params]
        deps = [by_name[c]["f"].name for c in f.calls if c in by_name]
        dispatch_functions[f.name] = {"file": f"{f.name}.ets", "params": params, "returns": k["returns"], "errors": k["errors"],
                                       "allowedImports": [{"from": f"./{d}", "name": d} for d in deps]}
        (project / "target" / f"{f.name}.ets").write_text(templates_arkts.placeholder_ets(f.name, params, k["returns"]), encoding="utf-8", newline="\n")
        view = "\n\n".join(f.needs + [f.source]) + "\n"
        (project / "view" / f"{f.name}.ts").write_text(view, encoding="utf-8", newline="\n")

        sig = ", ".join(f"{p['name']}: {p['type']}" for p in params)
        notes = f"Exact signature: `export function {f.name}({sig}): {k['returns']}`. "
        notes += ("ArkTS only: function declarations and (for pieces with an approved dependency) one named import "
                  "of that dependency are the only top-level statements permitted; no eval/Function/require/globalThis/"
                  "console/Reflect/Proxy/Date/setTimeout/setInterval/fetch, no dynamic property access. ")
        if k["errors"]:
            notes += f"Where the original throws, throw `new Error(\"<code>\")` with exactly one of: {', '.join(k['errors'])}; otherwise return the value directly. "
        if deps:
            notes += "Import and call the already migrated " + ", ".join(f"`{d}` from './{d}'" for d in deps) + " instead of re-implementing them. "
        notes += ("Numbers and text must match the original exactly. The signature is fixed and every input is inside "
                  "the declared input range, so handle nothing beyond it.")
        shown = []
        for c in k["dev"]:
            o = k["seen"][c["case_id"]]
            result = json.dumps(o["value"], ensure_ascii=False) if o["status"] == "ok" else f"throws {o['error_code']}"
            if len(result) <= 300 and len(shown) < 8 and (len(shown) < 4 or result not in [r for _, r in shown]):
                shown.append((json.dumps(c["input"], ensure_ascii=False), result))
        if shown:
            notes += " What the original really returns: " + "; ".join(f"{i} -> {r}" for i, r in shown) + "."

        for c in k["dev"] + k["hidden"]:
            c["chunk_id"] = k["id"]
        (project / "chunks" / f"{k['id']}.json").write_text(json.dumps({
            "schema_version": 1, "chunk_id": k["id"], "profile": name, "source_files": [f"view/{f.name}.ts"], "exports": [f.name],
            "write_allowlist": [f"target/{f.name}.ets"], "depends_on": [by_name[c]["id"] for c in deps], "worker_notes": notes,
            "contract_ids": [f"behavior-{f.name}"], "limits": {"attempts": 3, "verify_seconds_per_attempt": 300},
            "example_input": k["dev"][0]["input"], "input_domain": rules,
            "origin": {"file": source.name}}, indent=2) + "\n", encoding="utf-8", newline="\n")
        (project / "contracts" / f"behavior-{f.name}.json").write_text(json.dumps({
            "schema_version": 1, "contract_id": f"behavior-{f.name}", "version": 1,
            "behavior": f"For every input inside the declared input range, the ArkTS `{f.name}` returns exactly what the TypeScript "
                        "original returns: the same numbers, the same text character for character, and throws Error(<code>) where the original throws.",
            "guidance": []}, indent=2) + "\n", encoding="utf-8", newline="\n")

    dispatch = {"module": module, "functions": dispatch_functions}
    (project / "runners" / "dispatch.json").write_text(json.dumps(dispatch, indent=2) + "\n", encoding="utf-8", newline="\n")

    shutil.copytree(Path(__file__).resolve().parent / "ts_arkts" / "harness_template", project / "harness", dirs_exist_ok=True)
    # The local debug signing certificate (see runners/build.py::signing) is issued for one fixed
    # bundleName; DevEco rejects a signed build whose app.json5 bundleName doesn't match it exactly.
    # Every generated project reuses it, same as the hand-authored ts-arkts-core fixture; target.py
    # already handles two different projects sharing one bundleName (uninstall-and-retry on a
    # "sign info inconsistent" install).
    bundle = os.environ.get("PARITY_ARKTS_BUNDLE", "com.ratchet.arkts.smoke")
    app_json = project / "harness" / "AppScope" / "app.json5"
    app_json.write_text(app_json.read_text(encoding="utf-8").replace("__PARITY_BUNDLE__", bundle), encoding="utf-8", newline="\n")
    entry_dir = project / "harness" / "entry" / "src" / "main" / "ets" / "entryability"
    entry_dir.mkdir(parents=True, exist_ok=True)
    (entry_dir / "EntryAbility.ets").write_text(templates_arkts.entry_ability_ets(dispatch_functions), encoding="utf-8", newline="\n")

    (project / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for k in order for c in k["dev"]), encoding="utf-8", newline="\n")
    (project / "locked" / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for k in order for c in k["hidden"]), encoding="utf-8", newline="\n")
    (project / "profile.json").write_text(json.dumps({
        "schema_version": 1, "profile": name, "languages": {"source": "TypeScript", "target": "ArkTS"},
        "run_source": ["python", "runners/source.py"], "build_target": ["python", "runners/build.py"],
        "compile_target": ["python", "runners/build.py", "--unsigned"], "run_target": ["python", "runners/target.py"],
        "preflight": ["python", "runners/build.py", "--preflight"], "verify_seconds": 300,
        "frozen": ["profile.json", "chunks/*", "contracts/*", "legacy/*", "runners/*", "harness/*", "harness/*/*", "harness/*/*/*",
                   "harness/*/*/*/*", "harness/*/*/*/*/*", "harness/*/*/*/*/*/*", "cases.jsonl", "locked/*"],
        "oracle_paths": ["legacy", "cases.jsonl", "locked"], "locked_cases": "locked/cases.jsonl",
        "forbid_patterns": [{"regex": r"\b(eval|Function|require|globalThis|console|hilog|Reflect|Proxy)\b", "why": "dynamic execution and platform access are excluded"}],
        "skipped": skipped}, indent=2) + "\n", encoding="utf-8", newline="\n")

    if verify_build:
        built = subprocess.run(["python", "runners/build.py", "--unsigned"], cwd=project, capture_output=True, text=True, timeout=300)
        for leftover in project.glob("_arkts*"):
            leftover.unlink() if leftover.is_file() else shutil.rmtree(leftover, ignore_errors=True)
        if built.returncode != 0:
            raise SystemExit("the generated ArkTS scaffold does not compile (a bug in `parity new`):\n" + (built.stdout + built.stderr)[-1500:])

    say(f"project ready: projects/{name}   {len(order)} pieces, {sum(len(k['dev']) for k in order)} test inputs, {sum(len(k['hidden']) for k in order)} hidden")
    for fname, why in skipped.items():
        say(f"  skipped {fname}: {why}")
    return project
