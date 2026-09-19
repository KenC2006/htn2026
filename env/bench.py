"""Benchmark: how many functions of a translation are PROVEN (pass the visible tests and every hidden input)?

    python env/bench.py check <label> <candidates_root>     check one translation of every benchmark project (no model calls)
    python env/bench.py table                               print every arm recorded so far, side by side

<candidates_root>/<project>/target/*.rs holds the translation of each project. Results go to runs/bench/<label>.json.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "bench"
PROJECTS = ["bench-humanize-number", "bench-humanize-time", "bench-humanize-filesize", "bench-humanize-lists", "bench-statistics",
            "bench-colorsys", "bench-html", "bench-shlex", "bench-idna", "bench-inflection", "urllib-parse"]
WHY = {"REJECTED_BUILD": "does not compile", "REJECTED_BEHAVIOR": "wrong answers on the visible tests", "REJECTED_TEST": "crashes",
       "REJECTED_POLICY": "breaks a rule", "REJECTED_INTEGRATION": "breaks other functions"}


def read_run(run_id: str) -> dict:
    """{function: {"proven": bool, "why": str}} from a run's events."""
    events = [json.loads(x) for x in (ROOT / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    start = events[0]["payload"]
    names = {c: json.loads((Path(start["profile_dir"]) / "chunks" / f"{c}.json").read_text(encoding="utf-8"))["exports"][0] for c in start["chunks"]}
    out = {n: {"proven": False, "why": "not attempted"} for n in names.values()}
    for e in events:
        n = names.get(e.get("chunk_id"))
        if not n:
            continue
        p = e["payload"]
        if e["type"] == "candidate.rejected" and "compile" not in (e.get("attempt_id") or ""):
            out[n]["why"] = WHY.get(p.get("reason"), str(p.get("reason")))
        elif e["type"] == "chunk.blocked":
            out[n]["why"] = out[n]["why"] if out[n]["why"] != "not attempted" else str(p.get("reason"))[:60]
        elif e["type"] == "chunk.accepted":
            out[n] = {"proven": True, "why": "passed the visible tests"}
        elif e["type"] == "evaluation.locked":
            got, want = p["cases"].get("passed", 0), p["cases"].get("expected", 0)
            out[n] = {"proven": got == want, "why": f"hidden inputs {got}/{want}"}
    return out


def check(label: str, root: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result = {}
    for project in PROJECTS:
        run_id = f"bench-{label}-{project}"
        if not (ROOT / "runs" / run_id / "events.jsonl").exists():
            subprocess.run([sys.executable, "-m", "parity", "check", f"projects/{project}", str(root / project), "--author", label,
                            "--run-id", run_id, "--no-watch", "--no-tester"], cwd=ROOT, capture_output=True, text=True)
        result[project] = read_run(run_id)
        good = sum(v["proven"] for v in result[project].values())
        print(f"{project:<26}{good} of {len(result[project])} proven")
    (OUT / f"{label}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def table() -> None:
    arms = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(OUT.glob("*.json"))}
    if not arms:
        sys.exit("nothing recorded yet")
    print(f"{'library':<26}{'functions':<11}" + "".join(f"{a:<22}" for a in arms))
    totals = {a: 0 for a in arms}
    count = 0
    for project in PROJECTS:
        n = max((len(r.get(project, {})) for r in arms.values()), default=0)
        count += n
        row = f"{project.replace('bench-', ''):<26}{n:<11}"
        for a, r in arms.items():
            good = sum(v["proven"] for v in r.get(project, {}).values())
            totals[a] += good
            row += f"{good:<22}"
        print(row)
    print(f"{'TOTAL proven':<26}{count:<11}" + "".join(f"{f'{totals[a]} ({100 * totals[a] // max(count, 1)}%)':<22}" for a in arms))
    for a, r in arms.items():
        bad = [(p.replace("bench-", ""), f, v["why"]) for p, fs in r.items() for f, v in fs.items() if not v["proven"]]
        if bad:
            print(f"\n{a}: not proven")
            for p, f, why in bad:
                print(f"  {p}.{f}: {why}")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "check":
        check(sys.argv[2], Path(sys.argv[3]).resolve())
        table()
    elif len(sys.argv) == 2 and sys.argv[1] == "table":
        table()
    else:
        sys.exit(__doc__)
