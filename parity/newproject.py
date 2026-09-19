"""`parity new`: turn real Python code into a project the team can migrate to Rust.

scan (scan_python.py, static)  ->  settle each input's type and range  ->  RUN the original on generated inputs
to learn what it really returns  ->  write the project folder (same format as tests/flow_fixture, see CONTRACTS.md).

An AI may suggest types, ranges and example inputs. Nothing it says is trusted: every suggestion is tried on the
original code, and the expected outputs always come from running the original.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import templates
from .engine.domain import ALPHABET, coerce, in_domain
from .scan_python import Function, scan

from .paths import PROJECTS, ROOT  # noqa: E402,F401
RUST = {"int": "i64", "float": "f64", "str": "String", "bool": "bool"}
RUST_WORDS = {"as", "break", "const", "continue", "crate", "else", "enum", "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop",
              "match", "mod", "move", "mut", "pub", "ref", "return", "self", "static", "struct", "super", "trait", "true", "type", "unsafe",
              "use", "where", "while", "async", "await", "dyn", "abstract", "become", "box", "do", "final", "macro", "override", "priv",
              "typeof", "unsized", "virtual", "yield", "try", "json", "main"}
BIGGEST_OUTPUT = 20_000   # characters of JSON; beyond this the inputs are not realistic and nothing downstream can show them
DEV_CASES, HIDDEN_CASES = 40, 100


def rust_name(name: str) -> str:
    return name + "_" if name in RUST_WORDS else name


def rust_type(kind: str, nullable: bool = False) -> str:
    inner = f"Vec<{RUST[kind[5:-1]]}>" if kind.startswith("list[") else RUST[kind]
    return f"Option<{inner}>" if nullable else inner


# ── input ranges ────────────────────────────────────────────────────────────
def default_rule(f: Function, p, kind: str) -> dict:
    """A sensible range for one input when nobody suggested a better one. Defaults in the source steer it."""
    default = _default_value(f, p.name)
    rule: dict = {"type": kind}
    base = kind[5:-1] if kind.startswith("list[") else kind
    if base == "int":
        rule.update({"min": 0, "max": max(12, 3 * default)} if isinstance(default, int) and not isinstance(default, bool) and default >= 0
                    else {"min": -1_000_000, "max": 1_000_000})
    elif base == "float":
        rule.update({"min": -1_000_000.0, "max": 1_000_000.0})
    elif base == "str":
        rule.update({"choices": [default]} if isinstance(default, str) and kind == "str" else {"max_len": 24})
    if kind.startswith("list["):
        rule["max_items"] = 8
    if p.optional or default is None and p.has_default:
        rule["nullable"] = True
    return rule


def _default_value(f: Function, name: str):
    import ast
    node = ast.parse(f.source.lstrip()).body[0] if not f.source.startswith(" ") else None
    if node is None:
        return ...
    a = node.args
    positional = a.posonlyargs + a.args
    pairs = list(zip(positional[len(positional) - len(a.defaults):], a.defaults)) + [(k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
    for arg, d in pairs:
        if arg.arg == name:
            try:
                return ast.literal_eval(d)
            except Exception:  # noqa: BLE001
                return ...
    return ...


def make_value(rule: dict, rng: random.Random):
    if rule.get("nullable") and rng.random() < 0.15:
        return None
    kind = rule["type"]
    if rule.get("choices") and not kind.startswith("list[") and (rng.random() < 0.8 or kind == "str" and "max_len" not in rule):
        return coerce(rule, rng.choice(rule["choices"]))
    if kind.startswith("list["):
        item = {**rule, "type": kind[5:-1], "nullable": False}
        return [make_value(item, rng) for _ in range(rng.choice([0, 1, 1, 2, 3, rule.get("max_items", 8)]))]
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "int":
        lo, hi = int(rule["min"]), int(rule["max"])
        edges = [v for v in (lo, hi, 0, 1, -1, 2, 9, 10, 11, 99, 100, 101, 999, 1000, 1001, 999_999, 1_000_000, -10, -1000) if lo <= v <= hi]
        if rng.random() < 0.35:
            return rng.choice(edges)
        span = rng.choice([10, 1000, hi - lo])                   # small values are where most branches are
        return max(lo, min(hi, rng.randint(max(lo, -span), min(hi, span))))
    if kind == "float":
        lo, hi = float(rule["min"]), float(rule["max"])
        edges = [v for v in (lo, hi, 0.0, 0.5, 1.5, 2.5, 0.125, 0.1, 0.3, 1e-7, 1.0, -1.0, 999.5, 1000.0, 1234.5678, 0.045, 1e5, 123456.789) if lo <= v <= hi]
        r = rng.random()
        if r < 0.3:
            return rng.choice(edges)
        span = rng.choice([1.0, 100.0, hi - lo])
        v = rng.uniform(max(lo, -span), min(hi, span))
        return round(v, rng.choice([0, 1, 2, 3, 6])) if r < 0.8 else v
    if kind == "str":
        alphabet = rule.get("alphabet") or ALPHABET
        samples = rule.get("samples") or []
        r = rng.random()
        if samples and r < 0.45:                                 # a realistic value as it is
            return rng.choice(samples)
        if samples and r < 0.85:                                 # a realistic value, damaged: where parsers go wrong
            text = rng.choice(samples)
            for _ in range(rng.choice([1, 1, 2, 3])):
                at = rng.randrange(len(text) + 1)
                move = rng.random()
                if move < 0.35 and text:
                    text = text[:at] + text[at + 1:]
                elif move < 0.7:
                    text = text[:at] + rng.choice(alphabet) + text[at:]
                else:
                    other = rng.choice(samples)
                    text = text[:at] + other[rng.randrange(len(other) + 1):]
            return text[: rule.get("max_len", 200)]
        return "".join(rng.choice(alphabet) for _ in range(rng.choice([0, 1, 3, 5, 8, 12, rule.get("max_len", 24)])))
    raise ValueError(kind)


# ── optional AI suggestions (never trusted, only tried) ─────────────────────
def suggest(functions: list[Function]) -> dict:
    """Ask the workers' model for input types, realistic ranges and example inputs, one function at a time.

    Returns {function: suggestion}; a function is simply missing when the model gave nothing usable for it.
    """
    if not os.environ.get("API_KEY"):
        return {}
    import httpx
    from concurrent.futures import ThreadPoolExecutor
    ask = ("Say what inputs this Python function is meant to take. Reply with ONE JSON object and nothing else: "
           '{"<param>": {"type": "int" or "float" or "str" or "bool" or "list[int]" or "list[float]" or "list[str]", "min": number, "max": number, '
           '"max_len": int, "choices": [values], "samples": [20 strings], "nullable": true or false}, ..., "examples": [{"<param>": value, ...}, 6 of them]}. '
           "Pick ONE type per parameter (for a number that may be whole or fractional say float). Use min/max for numbers: a realistic "
           "range that still reaches every branch, never beyond 1e12 either way, written as plain JSON numbers. Use max_len for free text and "
           "choices for parameters that only make sense with a few values (format strings, modes, units). For every free-text parameter give "
           "samples: 20 realistic and varied values a real caller would pass (for a URL parameter real-looking URLs of every shape, for a name "
           "real names, and so on), plain ASCII, including the awkward ones that reach unusual branches. Each example is one full set of "
           "arguments that hits an interesting branch or edge.\n\n")

    def one(f: Function):
        for _ in range(2):
            try:
                r = httpx.post(os.environ["API_BASE"].rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {os.environ['API_KEY']}"},
                               json={"model": os.environ["MODEL_NAME"], "messages": [{"role": "user", "content": ask + "\n\n".join(f.needs[-6:] + [f.source])}],
                                     "temperature": 0, "max_tokens": 6000, "response_format": {"type": "json_object"}}, timeout=90)
                found = json.loads(re.search(r"\{.*\}", r.json()["choices"][0]["message"]["content"], re.DOTALL).group(0))
                if isinstance(found, dict):
                    return f.name, found.get(f.name) if isinstance(found.get(f.name), dict) else found
            except Exception:  # noqa: BLE001
                continue
        return f.name, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        return {name: idea for name, idea in pool.map(one, functions) if idea}


def words_in(f: Function) -> list[str]:
    """Quoted text in the function, its helpers and docstrings: realistic values to try for text inputs."""
    text = "\n".join(f.needs + [f.source])
    quoted = re.compile(r'"([^"\\\n]{1,24})"' + "|" + r"'([^'\\\n]{1,24})'")
    found = [a or b for a, b in quoted.findall(text)]
    return list(dict.fromkeys(w for w in found if all(ch in ALPHABET for ch in w)))[:60]


def rules_for(f: Function, hint: dict) -> dict | str:
    """The input range of every parameter, or a sentence saying which one cannot be settled."""
    rules = {}
    for p in f.params:
        idea = hint.get(p.name) if isinstance(hint.get(p.name), dict) else {}
        said = [t.strip() for t in str(idea.get("type") or "").split("|")]       # models often answer "int|float|str"
        pick = next((t for t in ("float", "int", "str", "bool") if t in said), None) or next((t for t in said if re.fullmatch(r"list\[(int|float|str|bool)\]", t)), None)
        kind = p.type or pick
        if kind is None:
            return f"input `{p.name}` has no type hint I support, and none could be suggested"
        rule = default_rule(f, p, kind)
        if kind in ("str", "list[str]") and "max_len" in rule:
            rule["choices"] = words_in(f)
        for key in ("min", "max", "max_len", "choices", "nullable"):
            if idea.get(key) not in (None, [], ""):
                rule[key] = idea[key]
        if rule.get("nullable") and not p.optional and not (p.has_default and _default_value(f, p.name) is None):
            rule["nullable"] = False               # the model may only say "can be null" where the source itself says so
        samples = [x for x in idea.get("samples") or [] if isinstance(x, str) and len(x) <= 200 and all(" " <= ch <= "~" for ch in x)]
        if kind == "str" and idea.get("choices"):
            rule.pop("max_len", None)              # a mode, unit or encoding name: only the listed values, never free text
        elif samples and kind == "str":
            rule["samples"] = samples[:30]
            rule["alphabet"] = ALPHABET + "".join(sorted(set("".join(samples)) - set(ALPHABET)))
            rule["max_len"] = max(int(rule.get("max_len") or 0), max(map(len, samples)) + 8)
            if not idea.get("choices"):
                rule.pop("choices", None)          # words harvested from the source are a poor stand-in once there are real samples
        if "min" in rule and "max" in rule:
            limit = 10**12
            rule["min"], rule["max"] = max(-limit, min(rule["min"], rule["max"])), min(limit, max(rule["min"], rule["max"]))
            if kind in ("int", "list[int]"):
                rule["min"], rule["max"] = int(rule["min"]), int(rule["max"])
        if rule.get("choices") and "max_len" in rule and kind == "str":
            pass                                   # free text plus some must-try values
        rules[p.name] = rule
    return rules


# ── what the original really returns ────────────────────────────────────────
def shape_of(values: list, prefer_tuple: bool) -> str | None:
    """The Rust type that holds every observed output, or None when they do not fit one type."""
    nullable = any(v is None for v in values)
    seen = [v for v in values if v is not None]
    if not seen:
        return None

    def scalar(vs) -> str | None:
        special = ("NaN", "Infinity", "-Infinity")             # how the runners write non-finite floats
        numeric = any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vs)
        kinds = {("bool" if isinstance(v, bool) else "int" if isinstance(v, int) else "float" if isinstance(v, float) or (numeric and v in special)
                  else "str" if isinstance(v, str) else "?") for v in vs}
        if kinds <= {"int", "float"}:
            return "f64" if "float" in kinds else "i64"
        return RUST[kinds.pop()] if len(kinds) == 1 and "?" not in kinds else None

    if all(isinstance(v, list) for v in seen):
        lengths = {len(v) for v in seen}
        if len(lengths) == 1 and 2 <= next(iter(lengths)) <= 4 and prefer_tuple:
            parts = [scalar([v[i] for v in seen]) for i in range(next(iter(lengths)))]
            inner = f"({', '.join(parts)})" if all(parts) else None
        else:
            flat = [x for v in seen for x in v]
            item = scalar(flat) if flat else None
            inner = f"Vec<{item}>" if item else None
    elif any(isinstance(v, list) for v in seen):
        inner = None
    else:
        inner = scalar(seen)
    return None if inner is None else f"Option<{inner}>" if nullable else inner


def default_expr(rust: str) -> str:
    if rust.startswith("Option<"):
        return "None"
    if rust.startswith("Vec<"):
        return "Vec::new()"
    if rust.startswith("("):
        return "(" + ", ".join(default_expr(t.strip()) for t in rust[1:-1].split(",")) + ")"
    return {"i64": "0", "f64": "0.0", "String": "String::new()", "bool": "false"}[rust]


def _run_original(project: Path, cases: list[dict]) -> dict:
    tmp = project / "_probe"
    tmp.mkdir(exist_ok=True)
    (tmp / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    ran = subprocess.run([sys.executable, str(project / "runners" / "source.py"), "--cases", str(tmp / "cases.jsonl"), "--out", str(tmp / "obs.jsonl")],
                         cwd=project, capture_output=True, text=True, timeout=300)
    if ran.returncode != 0:
        print(f"  (the original could not be run: {ran.stderr.strip().splitlines()[-1][:200] if ran.stderr.strip() else 'no error text'})")
    obs = {}
    if (tmp / "obs.jsonl").exists():
        obs = {o["case_id"]: o for o in map(json.loads, (tmp / "obs.jsonl").read_text(encoding="utf-8").splitlines())}
    shutil.rmtree(tmp, ignore_errors=True)
    return obs


def make_cases(name: str, piece: str, rules: dict, n: int, seed: int, examples: list, prefix: str) -> list[dict]:
    rng, cases, seen = random.Random(seed), [], set()
    pool = [e for e in examples if isinstance(e, dict) and set(e) == set(rules) and not any(in_domain(rules[k], v) for k, v in e.items())]
    while len(cases) < n and len(seen) < n * 20:
        value = pool.pop(0) if pool else {k: make_value(r, rng) for k, r in rules.items()}
        value = {k: coerce(rules[k], v) for k, v in value.items()}
        key = json.dumps(value, sort_keys=True)
        seen.add(key + str(len(seen)) if key in seen else key)
        if key in {json.dumps(c["input"], sort_keys=True) for c in cases}:
            continue
        cases.append({"schema_version": 1, "case_id": f"{prefix}-{name}-{len(cases):03d}", "chunk_id": piece, "export": name, "input": value})
    return cases


# ── the project folder ──────────────────────────────────────────────────────
def create(source: Path, name: str, chosen: list[str] | None = None, *, use_ai: bool = True, say=print) -> Path:
    """Build projects/<name>/ from a Python file or package folder. Returns the project folder."""
    source = Path(source).resolve()
    functions = scan(source)
    wanted = [f for f in functions if f.ok and (chosen is None and not f.name.startswith("_") or chosen is not None and f.name in chosen)]
    if not wanted:
        raise SystemExit("none of the chosen functions can be migrated")
    names = [f.name for f in wanted]
    if len(set(names)) != len(names):
        raise SystemExit(f"two chosen functions share a name ({sorted(n for n in names if names.count(n) > 1)[0]}); choose them from one file at a time")

    project = PROJECTS / name
    if project.exists():
        shutil.rmtree(project)
    for folder in ("legacy", "runners", "harness", "target", "chunks", "contracts", "view", "locked"):
        (project / folder).mkdir(parents=True)

    # the original, importable exactly as it is in its own package
    from .scan_python import _module_name
    for file in {f.file for f in wanted}:
        _, top = _module_name(file)
        package = file
        while package.parent != top:
            package = package.parent
        dest = project / "legacy" / package.name
        if package.is_dir() and not dest.exists():
            shutil.copytree(package, dest, ignore=shutil.ignore_patterns("__pycache__", "tests", "test", "*.pyc", "*.so", "*.pyd"))
        elif package.is_file():
            shutil.copy(package, dest)
    (project / "runners" / "source.py").write_text(templates.SOURCE_RUNNER, encoding="utf-8", newline="\n")
    (project / "runners" / "target.py").write_text(templates.TARGET_RUNNER, encoding="utf-8", newline="\n")
    (project / "runners" / "exports.json").write_text(json.dumps({f.name: f.module for f in wanted}, indent=2), encoding="utf-8", newline="\n")

    say(f"asking the model for realistic input ranges for {len(wanted)} functions…" if use_ai and os.environ.get("API_KEY") else "using default input ranges")
    hints = suggest(wanted) if use_ai else {}

    kept, skipped = [], {}
    for f in wanted:
        hint = hints.get(f.name) if isinstance(hints.get(f.name), dict) else {}
        rules = rules_for(f, hint)
        if isinstance(rules, str):
            skipped[f.name] = rules
            continue
        dev = make_cases(f.name, "?", rules, DEV_CASES, 1, hint.get("examples") or [], "case")
        hidden = make_cases(f.name, "?", rules, HIDDEN_CASES, 9001, [], "hidden")
        first, again = _run_original(project, dev + hidden), _run_original(project, dev + hidden)
        strip = lambda o: (o.get("status"), json.dumps(o.get("value")), o.get("error_code"))  # noqa: E731
        ok = [o for o in first.values() if o["status"] == "ok"]
        crashes = [o for o in first.values() if o["status"] == "crash"]
        errors = sorted({o["error_code"] for o in first.values() if o["status"] == "error"})
        if len(first) != len(dev + hidden) or crashes:
            skipped[f.name] = f"returns something I cannot carry across ({(crashes[0]['diagnostics'] if crashes else 'the original did not run')[:90]})"
        elif {k: strip(v) for k, v in first.items()} != {k: strip(v) for k, v in again.items()}:
            skipped[f.name] = "gave different answers for the same inputs when run twice"
        elif "TypeError" in errors or "AttributeError" in errors:
            skipped[f.name] = "rejects the input types I settled on (TypeError); it needs type hints"
        elif any(len(json.dumps(o.get("value"))) > BIGGEST_OUTPUT for o in first.values()):
            skipped[f.name] = (f"returns outputs over {BIGGEST_OUTPUT} characters for some inputs in the range I settled on; "
                               "give it type hints or narrower ranges so the inputs stay realistic")
        elif len(ok) < 0.5 * len(first):
            skipped[f.name] = f"raises {errors[0] if errors else 'errors'} on most inputs in the range I settled on"
        else:
            shape = shape_of([o["value"] for o in ok], f.returns_tuple)
            if shape is None:
                kinds = sorted({type(o["value"]).__name__ for o in ok})
                skipped[f.name] = f"returns different kinds of value for different inputs ({', '.join(kinds)}), which one Rust type cannot hold"
            else:
                kept.append({"f": f, "rules": rules, "returns": shape, "errors": errors, "dev": dev, "hidden": hidden, "seen": first})
    if not kept:
        shutil.rmtree(project)
        raise SystemExit("no function survived being run:\n" + "\n".join(f"  {k}: {v}" for k, v in skipped.items()))

    # order: what a piece calls comes first
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

    arms, mods = "", ""
    for k in order:
        f, mod = k["f"], rust_name(k["f"].name)
        params = [(p.name, rust_type(k["rules"][p.name]["type"], bool(k["rules"][p.name].get("nullable")))) for p in f.params]
        returns = f"Result<{k['returns']}, String>" if k["errors"] else k["returns"]
        signature = f"pub fn {mod}({', '.join(f'{rust_name(n)}: {t}' for n, t in params)}) -> {returns}"
        body = f"Ok({default_expr(k['returns'])})" if k["errors"] else default_expr(k["returns"])
        unused = ", ".join(f"_{rust_name(n)}: {t}" for n, t in params)
        (project / "target" / f"{mod}.rs").write_text(
            f"pub fn {mod}({unused}) -> {returns} {{\n    {body} // placeholder until piece {k['id']} is kept\n}}\n", encoding="utf-8", newline="\n")
        mods += f'#[path = "../target/{mod}.rs"]\nmod {mod};\n'
        call = f"{mod}::{mod}(" + ", ".join(f'<{t}>::from_j(input.get("{n}"))' for n, t in params) + ")"
        arms += f'        "{f.name}" => ' + (f"{call}.to_j(),\n" if k["errors"] else f'format!("{{{{\\"ok\\": {{}}}}}}", {call}.to_j()),\n')

        deps = [by_name[c]["id"] for c in f.calls if c in by_name]
        private = [c for c in f.calls if c not in by_name]
        view = "\n\n".join(f.needs + [f.source]) + "\n"
        (project / "view" / f"{f.name}.py").write_text(view, encoding="utf-8", newline="\n")
        notes = (f"The frozen harness declares `mod {mod};` from target/{mod}.rs. No external crates, no `unsafe`, no file, network or process access. "
                 f"Exact signature: `{signature}`. ")
        if k["errors"]:
            notes += (f"Where the Python original raises an exception, return Err with exactly the exception class name "
                      f"(seen so far: {', '.join(k['errors'])}); otherwise Ok(value). ")
        if deps:
            notes += "Call the already migrated " + ", ".join(f"`crate::{rust_name(c)}::{rust_name(c)}`" for c in f.calls if c in by_name) + " instead of re-implementing them. "
        if private:
            notes += f"The helpers {', '.join(private)} are shown with the source; port them as private functions inside your file. "
        notes += ("Text must match the original character for character, numbers exactly. "
                  "The signature is fixed and every input is inside the declared input range, so handle nothing beyond it. "
                  "The original runs with nothing configured: default locale, no translations loaded, no environment variables, no earlier calls. ")
        shown = []
        for c in k["dev"]:
            o = k["seen"][c["case_id"]]
            result = json.dumps(o["value"], ensure_ascii=False) if o["status"] == "ok" else f"raises {o['error_code']}"
            if len(result) > 300:
                continue
            if len(shown) < 8 and (len(shown) < 4 or result not in [r for _, r in shown]):
                shown.append((json.dumps(c["input"], ensure_ascii=False), result))
        notes += "What the original really returns: " + "; ".join(f"{i} -> {r}" for i, r in shown) + "."
        example = k["dev"][0]["input"]
        for c in k["dev"] + k["hidden"]:
            c["chunk_id"] = k["id"]
        (project / "chunks" / f"{k['id']}.json").write_text(json.dumps({
            "schema_version": 1, "chunk_id": k["id"], "profile": name, "source_files": [f"view/{f.name}.py"], "exports": [f.name],
            "write_allowlist": [f"target/{mod}.rs"], "depends_on": deps, "worker_notes": notes, "contract_ids": [f"behavior-{f.name}"],
            "limits": {"attempts": 3, "verify_seconds_per_attempt": 180}, "example_input": example, "input_domain": k["rules"],
            "origin": {"module": f.module, "file": str(f.file.name), "line": f.lineno}}, indent=2) + "\n", encoding="utf-8", newline="\n")

    (project / "harness" / "json.rs").write_text(templates.JSON_RS, encoding="utf-8", newline="\n")
    (project / "harness" / "main.rs").write_text(templates.MAIN_RS_HEAD + mods + templates.MAIN_RS_TAIL.replace("%ARMS%", arms), encoding="utf-8", newline="\n")
    (project / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for k in order for c in k["dev"]), encoding="utf-8", newline="\n")
    (project / "locked" / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for k in order for c in k["hidden"]), encoding="utf-8", newline="\n")
    # one contract per piece: a ruling about one function must not make the others stale
    for k in order:
        (project / "contracts" / f"behavior-{k['f'].name}.json").write_text(json.dumps({
            "schema_version": 1, "contract_id": f"behavior-{k['f'].name}", "version": 1,
            "behavior": f"For every input inside the declared input range, the Rust `{k['f'].name}` returns exactly what the Python original returns: "
                        "the same numbers, the same text character for character, and Err(<exception class name>) where the original raises.",
            "guidance": []}, indent=2) + "\n", encoding="utf-8", newline="\n")
    (project / "profile.json").write_text(json.dumps({
        "schema_version": 1, "profile": name, "languages": {"source": "Python", "target": "Rust"},
        "run_source": ["python", "runners/source.py"], "run_target": ["python", "runners/target.py"],
        "build_target": ["rustc", "-O", "harness/main.rs", "-o", "parity_target.exe"], "verify_seconds": 180,
        "frozen": ["profile.json", "chunks/*", "contracts/*", "legacy/*", "legacy/*/*", "legacy/*/*/*", "view/*", "harness/*", "runners/*", "cases.jsonl", "locked/*"],
        "oracle_paths": ["legacy", "view", "cases.jsonl", "locked"],
        "forbid_patterns": [{"regex": "\\bunsafe\\b", "why": "no unsafe code"}, {"regex": "\\b(todo|unimplemented)!", "why": "stub macro"},
                            {"regex": "std::(fs|net|process|env)\\b", "why": "the new code may not touch files, the network, processes or the environment"}],
        "locked_cases": "locked/cases.jsonl", "skipped": skipped}, indent=2) + "\n", encoding="utf-8", newline="\n")

    built = subprocess.run(["rustc", "-O", "harness/main.rs", "-o", "_check.exe"], cwd=project, capture_output=True, text=True)
    for leftover in project.glob("_check.*"):
        leftover.unlink()
    if built.returncode != 0:
        raise SystemExit("the generated Rust scaffold does not compile (a bug in `parity new`):\n" + built.stderr[-1500:])
    say(f"project ready: {os.path.relpath(project, Path.cwd()).replace(os.sep, '/') if Path.cwd().resolve() in project.resolve().parents else project}   {len(order)} pieces, {sum(len(k['dev']) for k in order)} test inputs, "
        f"{sum(len(k['hidden']) for k in order)} hidden")
    for fname, why in skipped.items():
        say(f"  skipped {fname}: {why}")
    return project
