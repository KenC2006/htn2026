"""Parity command line.  python -m parity <command>

  doctor                      check toolchains, framework, key and budget
  scan <profile_dir>          show what a migration would include, before spending any tokens
  new <file|folder|module>    make a project from real Python, C or TypeScript code: finds the functions that can be migrated, builds inputs and the scaffold
  run <profile_dir> [--resume --run-id X]   migrate with the agent team; --resume continues an interrupted run
  check <profile_dir> <candidate_dir> [--author NAME]   check a translation written by anyone (another model, a person)
  watch <run_id> [--replay]   live view of the agents, in plain words (run shows it by default in a terminal)
  status <run_id>             progress table rebuilt from the event log
  evaluate <run_id>           single-use locked evaluation: cases never seen by agents or repair loops
  export <run_id>             patch + report.md + receipts.json (refuses to call a stale/blocked run accepted)
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .paths import PROJECTS, ROOT, RUNS, WORK, project_dir


def _events(run_id: str) -> list[dict]:
    path = RUNS / run_id / "events.jsonl"
    if not path.exists():
        sys.exit(f"no such run: {run_id} (looked for {path})")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _load_profile(profile_dir: Path) -> tuple[dict, dict]:
    profile = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))
    chunks = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((profile_dir / "chunks").glob("*.json"))}
    return profile, chunks


def _why(log: str) -> str:
    """The line of a failed tool's output that says what is wrong: the last "Error: ..." line, else the last line."""
    lines = [x.strip() for x in log.splitlines() if x.strip()]
    return next((x.removeprefix("Error: ") for x in reversed(lines) if x.startswith("Error: ")), lines[-1] if lines else "no output")


# ───────────────────────── doctor ─────────────────────────

def doctor(_: argparse.Namespace) -> int:
    rows: list[tuple[str, bool, str, bool]] = []  # name, ok, detail, required

    def tool(name: str, argv: list[str], required: bool, why: str) -> None:
        exe = shutil.which(argv[0])
        if not exe:
            rows.append((name, False, f"not found ({why})", required))
            return
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            rows.append((name, True, (out.stdout or out.stderr).strip().splitlines()[0][:70], required))
        except Exception as e:  # noqa: BLE001
            rows.append((name, False, str(e)[:70], required))

    rows.append(("python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0], True))
    try:
        from importlib.metadata import version
        rows.append(("workswarm / openjiuwen", True, f"workswarm {version('workswarm')}, openjiuwen {version('openjiuwen')}", True))
    except Exception as e:  # noqa: BLE001
        rows.append(("workswarm / openjiuwen", False, str(e)[:70], True))
    tool("rustc", ["rustc", "--version"], True, "builds every translation")
    tool("gcc", ["gcc", "--version"], False, "C to Rust only: compiles the original")
    arkts = ROOT / "fixtures" / "telemetry-workbench" / "ts-arkts-core"      # the route's own check: DevEco Studio, signing, one running emulator
    try:
        out = subprocess.run([sys.executable, "runners/build.py", "--preflight"], cwd=arkts, capture_output=True, text=True, timeout=60)
        last = _why(out.stdout + out.stderr)
        rows.append(("TypeScript to ArkTS", out.returncode == 0, last.removeprefix("RuntimeError: ")[:110] + ("" if out.returncode == 0 else "  (env/setup-arkts.md)"), False))
    except Exception as e:  # noqa: BLE001
        rows.append(("TypeScript to ArkTS", False, str(e)[:70], False))
    for var in ("API_BASE", "API_KEY", "MODEL_NAME", "REVIEWER_MODEL"):
        rows.append((f"env {var}", bool(os.environ.get(var)), "set" if os.environ.get(var) else "missing: . env/activate-swarm.sh", var != "REVIEWER_MODEL"))
    if os.environ.get("API_KEY") and os.environ.get("API_BASE"):
        try:
            import httpx
            d = httpx.get(os.environ["API_BASE"].rstrip("/") + "/key", headers={"Authorization": f"Bearer {os.environ['API_KEY']}"}, timeout=20).json()["data"]
            rows.append(("model credit", d["usage"] < d["limit"] * 0.9, f"${d['usage']:.2f} used of ${d['limit']}", True))
        except Exception as e:  # noqa: BLE001
            rows.append(("model credit", False, f"could not reach provider: {str(e)[:50]}", True))
    bad = 0
    for name, ok, detail, required in rows:
        mark = "OK  " if ok else ("FAIL" if required else "warn")
        bad += (not ok) and required
        print(f"  [{mark}] {name:<26} {detail}")
    print("\nReady." if not bad else f"\n{bad} required check(s) failed.")
    return 1 if bad else 0


# ───────────────────────── scan ─────────────────────────

def scan(ns: argparse.Namespace) -> int:
    profile_dir = project_dir(ns.profile_dir)
    profile, chunks = _load_profile(profile_dir)
    problems = []
    cases = _jsonl(profile_dir / "cases.jsonl")
    print(f"Profile {profile['profile']}  ({profile_dir})")
    print(f"  build: {' '.join(profile.get('build_target', []))}\n")
    print(f"  {'chunk':<7}{'exports':<26}{'depends on':<14}{'cases':<7}writes")
    for cid, m in chunks.items():
        n = sum(1 for c in cases if c.get("chunk_id") == cid)
        print(f"  {cid:<7}{', '.join(m['exports']):<26}{', '.join(m.get('depends_on', [])) or '-':<14}{n:<7}{', '.join(m['write_allowlist'])}")
        for p in m["source_files"]:
            if not (profile_dir / p).exists():
                problems.append(f"{cid}: source file {p} is missing")
        for p in m["write_allowlist"]:
            if not (profile_dir / p).exists():
                problems.append(f"{cid}: no compiling placeholder at {p}")
        for d in m.get("depends_on", []):
            if d not in chunks:
                problems.append(f"{cid}: depends on unknown chunk {d}")
        for c in m.get("contract_ids", []):
            if not (profile_dir / "contracts" / f"{c}.json").exists():
                problems.append(f"{cid}: contract {c} has no file in contracts/")
        if not n:
            problems.append(f"{cid}: no cases tagged with this chunk_id")
    print("\n  Contracts:")
    for p in sorted((profile_dir / "contracts").glob("*.json")):
        c = json.loads(p.read_text(encoding="utf-8"))
        print(f"    {c['contract_id']}: {c['behavior']}")
    print(f"\n  Workers can never see or edit: {', '.join(profile.get('frozen', []))}")
    print(f"  Hidden from the candidate's build and run: {', '.join(profile.get('oracle_paths', []))}")
    print(f"  Limits: max 2 parallel workers, {max(m.get('limits', {}).get('attempts', 2) for m in chunks.values())} attempts per chunk")
    print("\n  " + ("Scope is valid." if not problems else "PROBLEMS:\n    " + "\n    ".join(problems)))
    return 1 if problems else 0


# ───────────────────────── new ─────────────────────────

def find_source(what: str) -> Path:
    """A path, or the name of an installed module (humanize.number). Unknown names are downloaded from PyPI into projects/_downloads."""
    if Path(what).exists():
        return Path(what).resolve()
    import importlib.util
    top = what.split(".")[0]
    try:
        spec = importlib.util.find_spec(what)
    except (ImportError, ValueError):
        spec = None
    if spec is None or not spec.origin or not spec.origin.endswith(".py"):
        dest = PROJECTS / "_downloads"
        if not (dest / top).exists() and not (dest / f"{top}.py").exists():
            print(f"downloading {top} from PyPI…")
            got = subprocess.run([sys.executable, "-m", "pip", "download", top, "--no-deps", "--only-binary", ":all:", "-d", str(dest / "_wheels"), "-q"],
                                 capture_output=True, text=True)
            if got.returncode != 0:
                sys.exit(f"cannot find {what} as a file, an installed module, or on PyPI:\n{got.stderr[-400:]}")
            import zipfile
            for wheel in (dest / "_wheels").glob("*.whl"):
                zipfile.ZipFile(wheel).extractall(dest)
        parts = what.split(".")
        base = dest.joinpath(*parts)
        for cand in (base.with_suffix(".py"), base / "__init__.py", base):
            if cand.exists():
                return cand.parent if cand.name == "__init__.py" and len(parts) == 1 else cand
        sys.exit(f"downloaded {top}, but it has no module {what}")
    origin = Path(spec.origin)
    return origin.parent if origin.name == "__init__.py" and "." not in what else origin


def new(ns: argparse.Namespace) -> int:
    from .newproject import create
    from .newproject import scan
    source = find_source(ns.source)
    functions = scan(source)
    can = [f for f in functions if f.ok]
    print(f"{source}\n  {len(can)} of {len(functions)} functions can be migrated\n")
    for f in functions:
        sig = ", ".join(f"{p.name}: {p.type or '?'}" for p in f.params)
        print(f"  {'yes' if f.ok else 'no '}  {f.name}({sig})" + (f"\n        cannot: {f.reason}" if f.reason else ""))
    if ns.list or not can:
        return 0 if can else 1
    chosen = [n.strip() for n in ns.functions.split(",")] if ns.functions else None
    name = ns.name or re.sub(r"[^a-z0-9]+", "-", ns.source.replace("\\", "/").rstrip("/").split("/")[-1].removesuffix(".py").lower()).strip("-")
    print()
    create(source, name, chosen, use_ai=not ns.no_ai)
    print(f"\nNext: parity run {name}")
    return 0


# ───────────────────────── run ─────────────────────────

def _preflight(profile_dir: Path) -> bool:
    from .engine.gate import _run
    profile, _ = _load_profile(profile_dir)
    if not profile.get('preflight'):
        return True
    code, log = _run(profile['preflight'], profile_dir, 30)
    if code != 0:
        why = _why(log)
        print("  this project cannot run on this machine yet: " + why.removeprefix("RuntimeError: "))
        return False
    return True


def run(ns: argparse.Namespace) -> int:
    if not _preflight(project_dir(ns.profile_dir)):
        return 1
    run_id = ns.run_id or datetime.now().strftime("run-%m%d-%H%M%S")
    script = ROOT / "workflows" / "migrate.py"
    if (RUNS / run_id / "events.jsonl").exists() and not ns.resume:
        sys.exit(f"run {run_id} already exists. Continue it with --resume, or choose another --run-id.")
    args = {"profile_dir": str(project_dir(ns.profile_dir)), "run_id": run_id, "resume": bool(ns.resume)}
    if ns.start_from:
        args["start_from"] = str(Path(ns.start_from).resolve())
    if ns.escalate is not None:
        args["escalate"] = ns.escalate or "none"
    args = json.dumps(args)
    RUNS.mkdir(parents=True, exist_ok=True)
    with (RUNS / f"{run_id}.out").open("a" if ns.resume else "w", encoding="utf-8") as out:
        proc = subprocess.Popen([sys.executable, "-m", "parity.framework.run", str(script), "--args", args,
                                 "--token-limit", str(ns.token_limit)], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
        if sys.stdout.isatty() and not ns.no_watch:
            from .view import watch as live_view
            try:
                live_view(RUNS / run_id, proc=proc)
            except KeyboardInterrupt:
                proc.terminate()
        code = proc.wait()
    status(argparse.Namespace(run_id=run_id))
    print(f"\nNext: parity export {run_id}")
    return code


# ───────────────────────── check ─────────────────────────

def check(ns: argparse.Namespace) -> int:
    """Check a translation written by anyone (another model, a person) with the same checker, then the hidden test set."""
    import asyncio

    from .engine import gate
    from .engine.contracts import ContractLedger
    from .engine.evaluate import locked_evaluate
    from .engine.events import EventLog
    from .engine.integrator import Integrator

    profile_dir, cand_dir = project_dir(ns.profile_dir), Path(ns.candidate_dir).resolve()
    run_id = ns.run_id or datetime.now().strftime("check-%m%d-%H%M%S")
    run_dir = RUNS / run_id
    if (run_dir / "events.jsonl").exists():
        sys.exit(f"run {run_id} already exists; choose another --run-id.")
    profile, chunks = _load_profile(profile_dir)
    if not _preflight(profile_dir):
        return 1
    events = EventLog(run_dir, run_id)
    gate.freeze(profile_dir)
    ledger = ContractLedger(profile_dir, run_dir, chunks, events)
    cases_path = run_dir / "cases.jsonl"          # the run's own copy: inputs the tester finds are added to it
    cases_path.write_bytes((profile_dir / "cases.jsonl").read_bytes())
    integ = Integrator(profile_dir, run_dir, cases_path, ledger, events)
    events.emit("run.started", actor="scheduler", profile=profile["profile"],
                payload={"chunks": list(chunks), "profile_dir": str(profile_dir), "mode": f"outside: {ns.author}", "reused": []})
    ctx = None
    if not ns.no_tester and os.environ.get("API_KEY"):
        import logging
        logging.disable(logging.WARNING)          # the agent framework logs every step to the console
        from .engine.tools import RunContext
        from .framework.team import TEAM
        ctx = RunContext(profile_dir, run_dir, cases_path, profile, chunks, ledger, integ, events)
        TEAM.reset(run_id)
        ctx.register_tester(model=os.environ.get("TESTER_MODEL") or os.environ.get("REVIEWER_MODEL"))
    blocked, todo = {}, list(chunks)
    sem = asyncio.Semaphore(min(8, os.cpu_count() or 4))

    async def one(cid: str):
        missing = [d for d in chunks[cid].get("depends_on", []) if d not in integ.accepted]
        paths = chunks[cid]["write_allowlist"]
        if missing or not all((cand_dir / p).exists() for p in paths):
            blocked[cid] = "a function it calls was not kept" if missing else "no Rust file for it in that folder"
            events.emit("chunk.blocked", actor="scheduler", chunk_id=cid, payload={"reason": blocked[cid]})
            return None
        cand = {"files": [{"path": p, "content": (cand_dir / p).read_text(encoding="utf-8")} for p in paths],
                "contract_hashes": ledger.hashes(chunks[cid].get("contract_ids", []))}
        for f in cand["files"]:
            events.emit("worker.wrote", actor=ns.author, chunk_id=cid, payload=f)
        overlay = integ.files_of(chunks[cid].get("depends_on", []))
        async with sem:
            v = await asyncio.to_thread(gate.check, profile_dir, cid, cand, cases_path, run_dir, attempt_id=f"{cid}:{ns.author}-1",
                                        current_contract_hashes=ledger.hashes(), events=events, overlay=overlay)
        if v.accepted and ctx is not None:     # the tester attacks what passed the fixed cases
            ctx.under_test[cid], ctx.tester_calls[cid], ctx.found[cid] = (cand, overlay), 0, []
            events.emit("tester.started", actor="scheduler", chunk_id=cid, payload={})
            try:
                _, _, report = await TEAM.ask(f"tester-{cid}", ctx.tester_query(cid, cand))
            except Exception as e:  # noqa: BLE001
                report = {"summary": f"tester failed: {e}"}
            found = ctx.found.pop(cid, [])
            events.emit("tester.finished", actor="tester", chunk_id=cid,
                        payload={"found": len(found), "summary": ((report or {}).get("summary") or "")[:400]})
            if found:
                async with sem:
                    v = await asyncio.to_thread(gate.check, profile_dir, cid, cand, cases_path, run_dir,
                                                attempt_id=f"{cid}:{ns.author}-1-after-tester",
                                                current_contract_hashes=ledger.hashes(), events=events, overlay=overlay)
        return cid, cand, v

    async def schedule() -> None:
        while todo:
            ready = [c for c in todo if set(chunks[c].get("depends_on", [])) <= set(integ.accepted) | set(blocked)]
            wave = list(ready or todo)                      # a snapshot: the loop below removes from todo
            for cid in wave:
                todo.remove(cid)
            for got in await asyncio.gather(*[one(c) for c in wave]):
                if got is None:
                    continue
                cid, cand, v = got
                if v.accepted:                              # one at a time, in wave order: the integrator is the only writer
                    v = await asyncio.to_thread(integ.integrate, cid, cand)
                if not v.accepted:
                    blocked[cid] = v.status
                    events.emit("chunk.blocked", actor="scheduler", chunk_id=cid, payload={"reason": f"{v.status}: {v.detail[:200]}"})

    asyncio.run(schedule())
    events.emit("run.finished", actor="scheduler", payload={"profile": profile["profile"], "accepted": list(integ.accepted), "blocked": blocked,
                                                             "stale": [], "exportable": not blocked, "decisions": [], "accepted_tree": integ.tree_hash()})
    if integ.accepted and profile.get("locked_cases"):
        locked_evaluate(run_dir, profile_dir)
    if sys.stdout.isatty() and not ns.no_watch:
        from .view import watch as live_view
        live_view(run_dir, replay=True, speed=2.0)
    else:
        status(argparse.Namespace(run_id=run_id))
    return 0 if not blocked else 1


# ───────────────────────── watch ─────────────────────────

def watch(ns: argparse.Namespace) -> int:
    from .view import watch as live_view
    _events(ns.run_id)
    live_view(RUNS / ns.run_id, replay=ns.replay, speed=ns.speed)
    return 0


# ───────────────────────── status ─────────────────────────

def status(ns: argparse.Namespace) -> int:
    """One line per function: kept, still going, or needs a human, and why in plain words."""
    ev = _events(ns.run_id)
    start = ev[0]["payload"]
    why = {"REJECTED_BUILD": "does not compile", "REJECTED_BEHAVIOR": "gives different answers than the original",
           "REJECTED_POLICY": "changed a file it may not touch", "REJECTED_INTEGRITY": "test files were changed",
           "REJECTED_TEST": "crashed on some tests", "REJECTED_INTEGRATION": "breaks functions already kept"}
    names = {}
    for c in start.get("chunks", []):
        try:
            names[c] = ", ".join(json.loads((Path(start["profile_dir"]) / "chunks" / f"{c}.json").read_text(encoding="utf-8"))["exports"])
        except Exception:  # noqa: BLE001
            names[c] = c
    state = {c: {"mark": " ", "state": "waiting", "tries": 0, "last": "", "found": 0} for c in names}
    for e in ev:
        c, t = e.get("chunk_id"), e["type"]
        if c not in state:
            continue
        s_ = state[c]
        if t == "worker.started":
            s_["mark"], s_["state"], s_["tries"] = "…", "being written", s_["tries"] + 1
        elif t == "candidate.rejected" and "compile" not in (e.get("attempt_id") or ""):
            s_["last"] = why.get(e["payload"].get("reason"), "was rejected")
        elif t == "tester.finished":
            s_["found"] += e["payload"].get("found", 0)
        elif t == "chunk.accepted":
            s_["mark"], s_["state"] = "✓", "kept"
        elif t == "chunk.blocked":
            s_["mark"], s_["state"] = ("–", "not written") if str(e["payload"].get("reason") or "").startswith("no Rust file") else ("✗", "needs a human")
            s_["last"] = s_["last"] or str(e["payload"].get("reason") or "")[:60]
    kept = sum(1 for x in state.values() if x["state"] == "kept")
    fin = any(e["type"] == "run.finished" for e in ev)
    who = start.get("mode", "team")
    # No run.finished and nothing written for 10 minutes: it was stopped from outside (a closed terminal, a killed process).
    quiet = not fin and (datetime.now().timestamp() - (RUNS / ns.run_id / "events.jsonl").stat().st_mtime) > 600
    print(f"{ns.run_id} · {'agent team' if who == 'team' else who.replace('outside: ', 'written by ')} · "
          f"{'finished' if fin else 'stopped before it finished' if quiet else 'still running'} · {kept} of {len(state)} kept\n")
    width = max((len(n) for n in names.values()), default=8) + 2
    for c, x in state.items():
        notes = []
        if x["tries"] > 1:
            notes.append(f"{x['tries']} tries")
        if x["found"]:
            notes.append(f"tester found {x['found']} bad input{'s' if x['found'] != 1 else ''}" + (", fixed" if x["state"] == "kept" else ""))
        if x["state"] != "kept" and x["last"]:
            notes.append(x["last"])
        print(f"  {x['mark']} {names[c]:<{width}}{x['state']:<16}{' · '.join(notes)}")
    hidden = [e for e in ev if e["type"] == "evaluation.locked"]
    if hidden:
        got, want = (sum(e["payload"]["cases"].get(k, 0) for e in hidden) for k in ("passed", "expected"))
        print(f"\n  hidden tests: {got} of {want} match the original" + ("" if got == want else "  ✗ passed the visible tests but is not a correct translation"))
    if quiet and who == "team":
        print(f"\n  what was kept is safe. Continue it:  parity run {Path(start.get('profile_dir') or '').name} --run-id {ns.run_id} --resume")
    rules = len(_jsonl(RUNS / ns.run_id / "decisions.jsonl"))
    if rules:
        print(f"  expert rules made during the run: {rules}  (python {ROOT / 'env' / 'show-run.py'} {ns.run_id} for the full story)")
    return 0


# ───────────────────────── export ─────────────────────────

def _locked_line(receipts: dict) -> str:
    results = {c: r["locked_evaluation"] for c, r in receipts.items() if isinstance(r.get("locked_evaluation"), dict)}
    if not results:
        return "- Locked evaluation (cases never used for repair): **NOT_RUN**. Run `python -m parity evaluate <run_id>`."
    passed = sum(r["cases"].get("passed", 0) for r in results.values())
    total = sum(r["cases"].get("expected", 0) for r in results.values())
    failed = [f"{c} (first: {(r.get('first_failure') or {}).get('case_id')})" for c, r in results.items() if r["status"] != "PASS"]
    return (f"- Locked evaluation (cases never used for repair): **{'PASS' if not failed else 'FAIL'}**, {passed}/{total} cases"
            + (f"; failing chunks: {', '.join(failed)}. Kept as a result, not repaired." if failed else ""))


def evaluate(ns: argparse.Namespace) -> int:
    from .engine.evaluate import locked_evaluate
    ev = _events(ns.run_id)
    profile_dir = Path(ns.profile_dir or ev[0]["payload"].get("profile_dir") or "").resolve()
    if not (profile_dir / "profile.json").exists():
        sys.exit("cannot find the profile; pass --profile-dir")
    summary = locked_evaluate(RUNS / ns.run_id, profile_dir, force=ns.force)
    print(f"Locked evaluation for {ns.run_id}: {summary['status']}  {summary.get('detail', '')}")
    for cid, r in summary.get("chunks", {}).items():
        print(f"  {cid:<6}{r['status']:<6}{r['cases'].get('passed', 0)}/{r['cases'].get('expected', 0)}"
              + (f"   first failure: {json.dumps(r['first_failure'])[:200]}" if r["status"] != "PASS" else ""))
    return 0 if summary["status"] == "PASS" else 1


def export(ns: argparse.Namespace) -> int:
    run_dir = RUNS / ns.run_id
    ev = _events(ns.run_id)
    start, fin = ev[0]["payload"], next((e["payload"] for e in reversed(ev) if e["type"] == "run.finished"), None)
    profile_dir = Path(ns.profile_dir or start.get("profile_dir") or "").resolve()
    if not (profile_dir / "profile.json").exists():
        sys.exit("cannot find the profile; pass --profile-dir")
    profile, chunks = _load_profile(profile_dir)
    receipts = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((run_dir / "receipts").glob("*.json"))}
    decisions = _jsonl(run_dir / "decisions.jsonl")
    verdicts = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run_dir / "verdicts").glob("*.json"))]
    accepted = [c for c, r in receipts.items() if r["status"] == "ACCEPTED"]
    exportable = bool(fin) and bool(fin.get("exportable")) and len(accepted) == len(chunks)

    out = run_dir / "export"
    out.mkdir(exist_ok=True)
    patch = []
    for p in sorted((run_dir / "accepted").rglob("*")):
        if p.is_file():
            rel = p.relative_to(run_dir / "accepted").as_posix()
            old = (profile_dir / rel).read_text(encoding="utf-8").splitlines(keepends=True) if (profile_dir / rel).exists() else []
            patch += difflib.unified_diff(old, p.read_text(encoding="utf-8").splitlines(keepends=True), f"a/{rel}", f"b/{rel}")
    (out / "migration.patch").write_text("".join(patch), encoding="utf-8", newline="\n")
    (out / "receipts.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8", newline="\n")
    (out / "decisions.json").write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8", newline="\n")

    n = lambda t: sum(1 for e in ev if e["type"] == t)  # noqa: E731
    rejections = [v for v in verdicts if v["status"].startswith("REJECTED") and "compile" not in v["attempt_id"]]
    blocked = [(e["chunk_id"], e["payload"].get("reason")) for e in ev if e["type"] == "chunk.blocked"]
    lines = [f"# Parity report: {ns.run_id}", "",
             f"**Status: {'ACCEPTED, exportable' if exportable else 'NOT EXPORTABLE'}**  ",
             f"Profile `{profile['profile']}`, mode `{start.get('mode', 'team')}`, {len(accepted)}/{len(chunks)} chunks accepted.  ",
             f"Accepted tree `{(fin or {}).get('accepted_tree', '?')[:16]}`, fixture `{next(iter(receipts.values()), {}).get('fixture_hash', '?')[:16]}`.", "",
             "## What moved", "", "| Chunk | Exports | Files | Cases on the final tree |", "|---|---|---|---|"]
    for c in accepted:
        r = receipts[c]["cases"]
        lines.append(f"| {c} | {', '.join(chunks[c]['exports'])} | {', '.join(chunks[c]['write_allowlist'])} | {r['passed']}/{r['expected']} |")
    lines += ["", "Patch: `migration.patch`. Nothing is pushed or merged for you.", "",
              "## What was checked", "",
              "Every accepted chunk was built with the real target compiler and run against the original implementation's outputs, "
              "case by case, then re-checked on the full accepted tree at integration. Workers could not edit tests, runners, the harness "
              "or the original source, and the candidate's build had no access to the original or to credentials.", "",
              f"- Gate rejections: **{len(rejections)}**; stale candidates re-dispatched: **{n('candidate.stale')}**",
              f"- Worker questions to the steward: **{n('worker.question')}**; steward probes of the original: **{n('tool.probe_source')}**; "
              f"decisions recorded: **{len(decisions)}**", _locked_line(receipts), ""]
    if rejections:
        lines += ["### Rejected candidates", ""]
        for v in rejections:
            cx = v.get("counterexample") or {}
            lines.append(f"- `{v['attempt_id']}` **{v['status']}** at {v['stage']}: {v['detail'][:200]}")
            if cx:
                lines.append(f"  - input `{json.dumps(cx.get('input'))}` original `{json.dumps((cx.get('source') or {}).get('value'))}` "
                             f"candidate `{json.dumps((cx.get('target') or {}).get('value'))}`")
        lines.append("")
    if decisions:
        lines += ["## Shared decisions", ""]
        for d in decisions:
            lines += [f"**{d['decision_id']}** `{d['contract_id']}` v{d['supersedes']} → v{d['version']}, affects {', '.join(d['affected_chunks'])}  ",
                      f"Q: {d['question'][:300]}  ", f"Ruling: {d['ruling'][:500]}  ", f"Evidence: {', '.join(d['evidence_refs'])[:200]}", ""]
    lines += ["## What remains unsupported or blocked", ""]
    lines += [f"- {c}: {why}" for c, why in blocked] or ["- Nothing blocked in this run."]
    lines += ["- Finite testing within the declared input domain. This is evidence, not a proof of equivalence.",
              "- Candidates run as ordinary local processes without the original source or credentials; not a hardened sandbox.", "",
              "## Reproduce", "", "```", ". env/activate-swarm.sh",
              f"python -m parity scan {profile_dir.name}",
              f"python -m parity run {profile_dir.name}",
              "```", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"{'Exported' if exportable else 'Not everything was kept (the report says what is missing)'}: {out}")
    for f in sorted(out.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size} bytes)")
    kept = RUNS / ns.run_id / "accepted"
    if WORK != ROOT and kept.exists():                      # working in someone's own folder: put the new code where they can see it
        new = _load_profile(profile_dir)[0].get("languages", {}).get("target", "Rust")
        dest = Path.cwd() / f"{profile_dir.name}-{new.lower()}"
        shutil.copytree(kept, dest, dirs_exist_ok=True)
        print(f"\nThe proven {new} is in {dest.name}/")
    return 0 if exportable else 1


def project_name(source: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", source.replace("\\", "/").rstrip("/").split("/")[-1].removesuffix(".py").lower()).strip("-")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from .shell import shell
        return shell()
    if argv[0] == "check" and len(argv) == 2 and argv[1].endswith(".py"):      # parity check file.py: what can be migrated?
        argv = ["new", argv[1], "--list"]
    if argv[0] == "migrate" and len(argv) >= 2:                                 # parity migrate file.py [folder]
        name = project_name(argv[1])
        if not (PROJECTS / name / "profile.json").exists():
            code = main(["new", argv[1], "--name", name])
            if code:
                return code
        return main(["run", name] + (["--start-from", argv[2]] if len(argv) > 2 else []))
    ap = argparse.ArgumentParser(prog="parity", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=doctor)
    p = sub.add_parser("scan"); p.add_argument("profile_dir"); p.set_defaults(fn=scan)
    p = sub.add_parser("new", help="make a project from real Python code: a file, a folder, or a module name such as humanize.number")
    p.add_argument("source"); p.add_argument("--name"); p.add_argument("--functions", help="comma-separated; default: every public function that qualifies")
    p.add_argument("--list", action="store_true", help="only show which functions qualify"); p.add_argument("--no-ai", action="store_true", help="default input ranges, no model call")
    p.set_defaults(fn=new)
    p = sub.add_parser("run"); p.add_argument("profile_dir", nargs="?", help="project folder or name; default: the one made most recently here")
    p.add_argument("--run-id"); p.add_argument("--token-limit", type=int, default=3000000)   # a 10-function run on the cheap models used 1.07M
    p.add_argument("--resume", action="store_true", help="continue an interrupted run: keeps accepted chunks whose receipts still hold")
    p.add_argument("--no-watch", action="store_true", help="do not show the live view")
    p.add_argument("--escalate", help="stronger models, comma separated: a function that runs out of tries moves up to the next one "
                                      "(default: ESCALATE_MODELS in env/secrets.env; pass 'none' to turn it off)")
    p.add_argument("--start-from", help="folder with a translation someone already wrote: it is checked first, agents only fix what fails")
    p.set_defaults(fn=run)
    p = sub.add_parser("check", help="check a translation written by anyone with the same checker and hidden test set")
    p.add_argument("profile_dir"); p.add_argument("candidate_dir"); p.add_argument("--author", default="outside")
    p.add_argument("--run-id"); p.add_argument("--no-watch", action="store_true")
    p.add_argument("--no-tester", action="store_true", help="fixed cases and hidden test set only, no AI tester"); p.set_defaults(fn=check)
    p = sub.add_parser("watch"); p.add_argument("run_id"); p.add_argument("--replay", action="store_true", help="play a finished run back")
    p.add_argument("--speed", type=float, default=1.0); p.set_defaults(fn=watch)
    p = sub.add_parser("status"); p.add_argument("run_id"); p.set_defaults(fn=status)
    p = sub.add_parser("evaluate"); p.add_argument("run_id"); p.add_argument("--profile-dir")
    p.add_argument("--force", action="store_true", help="re-run a single-use locked evaluation"); p.set_defaults(fn=evaluate)
    p = sub.add_parser("export"); p.add_argument("run_id"); p.add_argument("--profile-dir"); p.set_defaults(fn=export)
    ns = ap.parse_args(argv)
    return ns.fn(ns) or 0


if __name__ == "__main__":
    sys.exit(main())
