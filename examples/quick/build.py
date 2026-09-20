"""Builds the three quick demos (about a minute each) as runs that `parity watch <id> --replay` plays back.

  python quick/build.py            from the demo folder. Makes .parity/runs/quick-python, quick-c, quick-arkts.
  quick\\play python | c | arkts    plays one.

quick-python is a REAL run (the cheap-model jellyfish run, 66 minutes) cut down to three functions: one that passed first
time, one that failed three times and was moved to a stronger model, one the tester broke. Nothing in it is written by hand.
quick-c and quick-arkts are SCRIPTED: the real run passed everything first time, so a failed first attempt is put in front of
the real accepted code. The failure itself is real: that wrong code was run through the real checker (see wrong/), and the
rejection, the failing input and the two answers are what the checker reported. The screen says "scripted replay".
"""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
RUNS = DEMO / ".parity" / "runs"
HERE = Path(__file__).parent

# seconds the replay rests after each kind of event (the view adds its own time for typing code out)
PACE = {"worker.wrote": 0.6, "candidate.rejected": 2.4, "candidate.verified": 1.0, "chunk.accepted": 1.2, "worker.escalated": 2.4,
        "decision.recorded": 2.0, "steward.answered": 1.2, "worker.question": 1.6, "planner.question": 1.6, "tester.finished": 1.4,
        "tool.try_inputs": 1.3, "tool.probe_source": 0.9, "evaluation.locked": 0.5, "run.finished": 2.0, "expert.hint": 2.2, "plan.accepted": 1.0}


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write(run: str, events: list[dict], label: str, profile_dir: Path, chunks: list[str]) -> None:
    t = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
    first = events[0]["payload"]
    first.update(label=label, profile_dir=str(profile_dir), chunks=chunks, levels=[[c for c in level if c in chunks] for level in first.get("levels", [])])
    first["levels"] = [x for x in first["levels"] if x] or [chunks]
    out = []
    for n, e in enumerate(events, 1):
        e = {**e, "run_id": run, "sequence": n, "timestamp": t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"}
        out.append(json.dumps(e, ensure_ascii=False))
        t += timedelta(seconds=PACE.get(e["type"], 0.4))
    (RUNS / run / "events.jsonl").write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"{run}: {len(out)} events, about {int((t - datetime(2026, 9, 20, 12, tzinfo=timezone.utc)).total_seconds())} s before code typing")


def cut(events: list[dict], keep: list[str], planning: int = 1) -> list[dict]:
    """The events of the kept functions, the start of planning, and the expert's work while a kept function is waiting on it."""
    out, waiting, rulings = [], False, 0
    for e in events:
        c, t = e.get("chunk_id"), e["type"]
        if t in ("run.started", "plan.accepted", "run.finished"):
            out.append(e)
        elif t == "tool.read_source":
            if c in keep:
                out.append(e)
        elif t == "planner.question":
            rulings += 1
            waiting = rulings <= planning
            if waiting:
                out.append({**e, "chunk_id": c if c in keep else keep[0]})
        elif c is None:                                   # the expert's probes and rulings carry no function id
            if waiting:
                out.append(e)
        elif c in keep:
            out.append(e)
            if t in ("worker.question", "steward.consulted"):
                waiting = True
        if t == "steward.answered":
            if waiting and c not in keep and out and out[-1] is not e:
                out.append({**e, "chunk_id": keep[0]})
            waiting = False
    fin = out[-1]["payload"]
    fin["accepted"] = [c for c in fin.get("accepted", []) if c in keep]
    fin["blocked"] = {}
    return out


def copy_run(source: Path, run: str) -> None:
    dest = RUNS / run
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("candidates", "workspace*", "*.exe", "*.pdb", "export"))


def python_demo() -> None:
    old = DEMO.parent / "demo-archive" / "2026-09-19-jellyfish-10of10" / ".parity"
    project = HERE / "projects" / "jellyfish"
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(old / "projects" / "jellyfish", project)
    copy_run(old / "runs" / "team-175048", "quick-python")
    keep = ["F6", "F2", "F7"]                             # hamming_distance, levenshtein_distance, nysiis
    events = cut(load(old / "runs" / "team-175048" / "events.jsonl"), keep)
    write("quick-python", events, "replay of a real 66 minute run, cut to 3 functions", project, keep)


if __name__ == "__main__":
    RUNS.mkdir(parents=True, exist_ok=True)
    which = sys.argv[1:] or ["python", "c", "arkts"]
    if "python" in which:
        python_demo()
    if "c" in which or "arkts" in which:
        from scripted import c_demo, arkts_demo
        if "c" in which:
            c_demo()
        if "arkts" in which:
            arkts_demo()
