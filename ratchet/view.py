"""Live terminal view of a run, in plain words.  python -m ratchet watch <run_id> [--replay]

Reads only runs/<id>/events.jsonl, so it works on a run in progress, a finished run, or a replay.
"""
from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ACTOR_STYLE = {"planner": "magenta", "expert": "yellow", "checker": "cyan", "worker": "blue", "solo": "blue", "run": "white"}
REASONS = {"REJECTED_BUILD": "does not compile", "REJECTED_BEHAVIOR": "wrong output", "REJECTED_POLICY": "touched a file it may not",
           "REJECTED_INTEGRITY": "test files were changed", "REJECTED_TEST": "missing or crashed cases",
           "REJECTED_INTEGRATION": "breaks pieces already kept"}


def _ts(e: dict) -> float:
    return datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()


def _short(s: object, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _probe_lines(p: dict) -> str:
    try:
        raw = p.get("result") or "[]"
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:          # older runs cut the result off mid-row
            rows = json.loads(raw[: raw.rindex("}, {") + 1] + "]")
        calls = [f"{p.get('export')}({', '.join(f'{k}={v}' for k, v in r['input'].items())}) = "
                 f"{r['value'] if r['status'] == 'ok' else r.get('error_code') or r['status']}" for r in rows]
        return "   ".join(calls[:4]) + (f"   (+{len(calls) - 4} more)" if len(calls) > 4 else "")
    except Exception:  # noqa: BLE001
        return _short(p.get("input"), 120)


class Board:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir, self.run_id, self.title, self.mode = run_dir, run_dir.name, "", "team"
        self.caught: list[str] = []
        self.pieces: dict[str, dict] = {}
        self.feed: deque[tuple[float, str, str, str]] = deque(maxlen=300)   # seconds, actor kind, who, text
        self.rules: list[dict] = []
        self.t0 = self.now = 0.0
        self.finished: dict | None = None
        self.hidden: str = ""

    def _counterexample(self, e: dict, export: str) -> str:
        for f in (self.run_dir / "verdicts").glob(f"{e.get('chunk_id')}-*.json"):
            v = json.loads(f.read_text(encoding="utf-8"))
            cx = v.get("counterexample")
            if v.get("attempt_id") == e.get("attempt_id") and cx:
                call = f"{export}({', '.join(f'{k}={x}' for k, x in cx['input'].items())})" if isinstance(cx.get("input"), dict) else json.dumps(cx.get("input"))
                got = lambda o: o.get("value") if o.get("status") == "ok" else o.get("error_code") or o.get("status")  # noqa: E731
                return f"{call}   original = {got(cx['source'])}   new code = {got(cx['target'])}"
        return ""

    def say(self, e: dict, kind: str, who: str, text: str) -> None:
        self.feed.append((_ts(e) - self.t0, kind, who, text))

    def apply(self, e: dict) -> None:
        t, p, c = e["type"], e["payload"], e.get("chunk_id") or ""
        self.now = _ts(e)
        piece = self.pieces.get(c)
        worker = "one agent" if self.mode == "single-agent" else f"worker {c}"
        if t == "run.started":
            self.t0, self.mode = _ts(e), p.get("mode", "team")
            try:
                prof = json.loads((Path(p["profile_dir"]) / "profile.json").read_text(encoding="utf-8"))
                lang = prof.get("languages", {})
                self.title = f"{lang.get('source', '?')} → {lang.get('target', '?')}"
                for cid in p.get("chunks", []):
                    m = json.loads((Path(p["profile_dir"]) / "chunks" / f"{cid}.json").read_text(encoding="utf-8"))
                    self.pieces[cid] = {"what": ", ".join(m["exports"]), "needs": m.get("depends_on", []), "state": "waiting",
                                        "style": "dim", "tries": 0, "asked": 0, "cases": ""}
            except Exception:  # noqa: BLE001
                self.pieces = {cid: {"what": "", "needs": [], "state": "waiting", "style": "dim", "tries": 0, "asked": 0, "cases": ""}
                               for cid in p.get("chunks", [])}
            self.say(e, "run", "run", f"{len(self.pieces)} pieces to rewrite" + (" with ONE agent" if self.mode == "single-agent" else ""))
        elif t == "tool.read_source":
            self.say(e, "planner", "planner", f"reads the source of {c}")
        elif t == "plan.accepted":
            order = "  then  ".join(" + ".join(level) + (" at the same time" if len(level) > 1 else "") for level in p["levels"])
            self.say(e, "planner", "planner", f"plan: {order}")
        elif t == "plan.fallback":
            self.say(e, "planner", "planner", "no usable plan, using the declared order")
        elif t == "planner.question" or (t == "worker.question" and e["actor"] == "planner"):
            self.say(e, "planner", "planner", f"asks the expert before anyone starts: {_short(p.get('question'), 170)}")
        elif t == "worker.question":
            if piece:
                piece["asked"] += 1
                piece["state"], piece["style"] = "asking the expert", "yellow"
            self.say(e, "worker", worker, f"asks the expert: {_short(p.get('question'), 170)}")
        elif t == "tool.probe_source":
            who = "one agent" if self.mode == "single-agent" else "expert"
            self.say(e, "expert" if who == "expert" else "solo", who, f"runs the ORIGINAL code: {_probe_lines(p)}")
        elif t == "decision.recorded":
            self.rules.append(p)
            self.say(e, "expert", "expert", f"NEW RULE {p['decision_id']} for {', '.join(p['affected_chunks'])}: {_short(p['ruling'], 190)}")
        elif t == "decision.rejected":
            self.say(e, "checker", "checker", f"refuses the expert's rule: {_short(p.get('detail') or p.get('reason'), 140)}")
        elif t == "steward.answered":
            if not p.get("decision_id"):
                self.say(e, "expert", "expert", f"answers {p.get('to') or c}: {_short(p.get('answer'), 170)}")
        elif t == "steward.consulted":
            self.say(e, "checker", "checker", f"sends the failure on {c} to the expert")
        elif t == "worker.started":
            if piece:
                piece["tries"] += 1
                piece["state"], piece["style"] = "being written", "blue"
            self.say(e, "worker", worker, "starts writing" + (f" (try {piece['tries']})" if piece and piece["tries"] > 1 else ""))
        elif t == "tool.check_compile":
            ok = p.get("result") == "BUILD_OK"
            self.say(e, "worker", worker, "compiles its code: " + ("OK" if ok else "does not compile yet"))
        elif t == "candidate.submitted":
            if ":integrate-" in (e.get("attempt_id") or ""):
                return
            if piece:
                piece["state"], piece["style"] = "being checked", "cyan"
            self.say(e, "worker", worker, f"hands in {', '.join(p.get('files', []))}")
        elif t == "candidate.rejected":
            if "compile" in (e.get("attempt_id") or ""):
                return
            why = REASONS.get(p.get("reason"), p.get("reason"))
            if piece:
                piece["state"], piece["style"] = f"REJECTED: {why}", "bold red"
            cx = self._counterexample(e, piece["what"] if piece else "")
            if cx:
                self.caught.append(f"{c}: {cx}")
            self.say(e, "checker", "checker", f"✗ REJECTS {c}: {why}. " + (cx or _short((p.get('detail') or '').splitlines()[0] if p.get('detail') else '', 150)))
        elif t == "candidate.stale" and e["actor"] == "integrator":
            if piece:
                piece["state"], piece["style"] = "re-checking under the new rule", "bold yellow"
            self.say(e, "checker", "checker", f"the new rule also covers {c}, which was already kept: checking it again")
        elif t == "candidate.stale":
            if piece:
                piece["state"], piece["style"] = "thrown out (old rule), redo", "bold yellow"
            self.say(e, "checker", "checker", f"{c} was written under an old rule: thrown out and sent back")
        elif t == "candidate.verified":
            if piece:
                piece["state"], piece["style"] = "matches, joining the others", "green"
            self.say(e, "checker", "checker", f"{c} matches the original: {p.get('detail')}")
        elif t == "chunk.accepted":
            if piece:
                piece["state"], piece["style"] = "KEPT ✓", "bold green"
                piece["cases"] = f"{p.get('cases', {}).get('passed', '?')}/{p.get('cases', {}).get('expected', '?')}"
            self.say(e, "checker", "checker", f"✓ {c} KEPT. {p.get('detail')} with everything kept so far")
        elif t == "chunk.revalidated":
            self.say(e, "checker", "checker", f"{c} re-checked under the new rule: still correct, no rewrite needed")
        elif t == "chunk.resumed":
            if piece:
                piece["state"], piece["style"] = "KEPT ✓ (from before)", "bold green"
            self.say(e, "run", "run", f"{c} already proven in the earlier run, reused")
        elif t == "chunk.blocked":
            if piece:
                piece["state"], piece["style"] = "NEEDS A HUMAN", "bold red"
            self.say(e, "run", "run", f"{c} needs a human: {_short(p.get('reason') or p.get('detail'), 140)}")
        elif t == "evaluation.locked":
            self.hidden = f"{p.get('status')}  {_short(p.get('detail'), 60)}"
            self.say(e, "checker", "checker", f"HIDDEN TEST SET (never seen by any agent): {self.hidden}")
        elif t == "run.finished":
            self.finished = p
            self.say(e, "run", "run", f"finished: {len(p.get('accepted', []))}/{len(self.pieces)} pieces kept")

    def render(self, height: int, width: int) -> Group:
        kept = sum(1 for s in self.pieces.values() if s["state"].startswith("KEPT"))
        head = Text.assemble(("  RATCHET  ", "bold black on green"), f"  {self.title}   ",
                             ("ONE AGENT" if self.mode == "single-agent" else "AGENT TEAM", "bold"),
                             f"   run {self.run_id}   {int(max(self.now - self.t0, 0))}s   ",
                             (f"{kept}/{len(self.pieces)} kept", "bold green" if kept == len(self.pieces) and kept else "bold"),
                             (f"   hidden test set: {self.hidden}" if self.hidden else ""))
        table = Table(expand=True, box=None, padding=(0, 1), header_style="dim")
        for col in ("piece", "function", "needs", "state", "tries", "asked expert", "cases matched"):
            table.add_column(col)
        for cid, s in self.pieces.items():
            table.add_row(cid, s["what"], ", ".join(s["needs"]) or "-", Text(s["state"], style=s["style"]), str(s["tries"] or ""),
                          str(s["asked"] or ""), s["cases"])
        rule = self.rules[-1] if self.rules else None
        rules = Panel(Text(_short(rule["ruling"], width * 2 - 12) if rule else "none yet", style="yellow" if rule else "dim"),
                      title=f"shared rules: {len(self.rules)}" + (f"   latest {rule['decision_id']} applies to {', '.join(rule['affected_chunks'])}" if rule else ""),
                      title_align="left", border_style="yellow" if rule else "dim")
        caught = Panel(Text("\n".join(self.caught[-3:]), style="bold red"), title=f"wrong answers caught by the checker: {len(self.caught)}",
                       title_align="left", border_style="red") if self.caught else None
        room = max(height - len(self.pieces) - 10 - (len(self.caught[-3:]) + 2 if self.caught else 0), 4)
        lines: list[Text] = []
        for secs, kind, who, text in reversed(self.feed):
            line = Text.assemble((f"{int(secs):>4}s ", "dim"), (f"{who:<10} ", f"bold {ACTOR_STYLE.get(kind, 'white')}"), text)
            if "✗" in text:
                line.stylize("red", 16)
            elif "✓" in text:
                line.stylize("green", 16)
            need = -(-len(line.plain) // max(width - 4, 20))
            if need > room:
                break
            room -= need
            lines.append(line)
        feed = Panel(Group(*reversed(lines)), title="what is happening", title_align="left", border_style="dim")
        return Group(*[x for x in (head, Text(""), table, rules, caught, feed) if x is not None])


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:      # a line still being written
            break
    return out


def watch(run_dir: Path, *, replay: bool = False, speed: float = 1.0, proc=None) -> None:
    console = Console()
    board, path, seen = Board(run_dir), run_dir / "events.jsonl", 0
    draw = lambda: board.render(console.size.height, console.size.width)  # noqa: E731
    with Live(draw(), console=console, refresh_per_second=8, screen=False, transient=False) as live:
        if replay:
            events = _read(path)
            for i, e in enumerate(events):
                board.apply(e)
                live.update(draw())
                if i + 1 < len(events):
                    time.sleep(max(min(_ts(events[i + 1]) - _ts(e), 2.5), 0.35) / speed)
            return
        idle_after_exit = 0
        while True:
            events = _read(path)
            for e in events[seen:]:
                board.apply(e)
            seen = len(events)
            if board.t0 and not board.finished:
                board.now = time.time()
            live.update(draw())
            if board.finished:
                return
            if proc is not None and proc.poll() is not None:
                idle_after_exit += 1
                if idle_after_exit > 5:
                    return
            time.sleep(0.4)
