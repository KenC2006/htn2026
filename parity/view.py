"""Live terminal view of a run, in plain words.  python -m parity watch <run_id> [--replay]

Reads only runs/<id>/ (events.jsonl, verdicts, accepted files), so it works on a run in progress,
a finished run, or a replay. The code of each piece is typed out next to the original as it arrives.
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

ACTOR_STYLE = {"tester": "red", "planner": "magenta", "expert": "yellow", "checker": "cyan", "worker": "blue", "run": "white"}
REASONS = {"REJECTED_BUILD": "does not compile", "REJECTED_BEHAVIOR": "wrong output", "REJECTED_POLICY": "touched a file it may not",
           "REJECTED_INTEGRITY": "test files were changed", "REJECTED_TEST": "missing or crashed cases",
           "REJECTED_INTEGRATION": "breaks pieces already kept"}
LEXERS = {".py": "python", ".rs": "rust", ".c": "c", ".h": "c", ".ts": "typescript", ".ets": "typescript", ".js": "javascript"}
THEME = "monokai"
CURSOR = "▌"


def _ts(e: dict) -> float:
    return datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()


def _short(s: object, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _lexer(path: str) -> str:
    return LEXERS.get(Path(path).suffix.lower(), "text")


def _only_function(text: str, names: list[str]) -> str:
    """The part of a source file that defines the exported function, so a shared file is not shown three times."""
    lines = text.splitlines()
    for name in names:
        start = next((i for i, l in enumerate(lines) if re.search(rf"\b{re.escape(name)}\s*\(", l) and l[:1] not in (" ", "\t", "#", "/")), None)
        if start is None:
            continue
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (" ", "\t", "}", ")")):
            end += 1
        return "\n".join(lines[start:end]).rstrip()
    return text.strip()


def _probe_count(p: dict) -> str:
    try:
        n = len(json.loads(p.get("result") or "[]"))
        return f"{n} input{'s' if n != 1 else ''}"
    except Exception:  # noqa: BLE001
        return "a few inputs"


def _plain_count(p: dict) -> str:
    cases = p.get("cases") or {}
    return f"{cases.get('passed', '?')} of {cases.get('expected', '?')} tests match the original"


def _piece(what: str = "", needs: list | None = None, src: str = "", src_path: str = "") -> dict:
    return {"what": what, "needs": needs or [], "state": "waiting", "style": "dim", "tries": 0, "asked": 0, "cases": "",
            "src": src, "src_path": src_path, "code": "", "path": "", "shown": 0.0, "note": "", "note_style": "dim", "touched": 0.0}


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
        self.hidden_pass = self.hidden_total = 0
        self.activity: dict | None = None        # what is being run right now: the expert's probes, the tests, the tester's attack
        self._inputs: dict[str, dict] = {}

    # ── helpers ────────────────────────────────────────────────────────────
    def _counterexample(self, e: dict, export: str) -> str:
        for f in (self.run_dir / "verdicts").glob(f"{e.get('chunk_id')}-*.json"):
            v = json.loads(f.read_text(encoding="utf-8"))
            cx = v.get("counterexample")
            if v.get("attempt_id") == e.get("attempt_id") and cx:
                call = f"{export}({', '.join(f'{k}={x}' for k, x in cx['input'].items())})" if isinstance(cx.get("input"), dict) else json.dumps(cx.get("input"))
                got = lambda o: o.get("value") if o.get("status") == "ok" else o.get("error_code") or o.get("status")  # noqa: E731
                return f"{call}   original = {got(cx['source'])}   new code = {got(cx['target'])}"
        return ""

    @staticmethod
    def _call(name: str, inputs: object, width: int = 32) -> str:
        args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in inputs.items()) if isinstance(inputs, dict) else json.dumps(inputs)
        return _short(f"{name}({args})", width)

    @staticmethod
    def _got(o: dict | None) -> str:
        if not o:
            return "?"
        return _short(json.dumps(o.get("value"), ensure_ascii=False), 20) if o.get("status") == "ok" else f"raises {o.get('error_code') or o.get('status')}"

    def _show(self, title: str, style: str, rows: list[tuple[str, str]], marks: str = "", foot: str = "") -> None:
        self.activity = {"title": title, "style": style, "rows": rows, "marks": marks, "foot": foot, "shown": 0.0}

    def _tests(self, e: dict, name: str, title: str) -> None:
        """Every test of this check, old answer next to new answer, read from what the checker saved."""
        folder = self.run_dir / "observations" / (e.get("attempt_id") or "").replace(":", "-")
        try:
            read = lambda f: {o["case_id"]: o for o in map(json.loads, (folder / f).read_text(encoding="utf-8").splitlines())}  # noqa: E731
            old, new = read("source_obs.jsonl"), read("target_obs.jsonl")
            cases = self.run_dir / "cases.jsonl"
            if cases.exists() and len(self._inputs) < len(old):
                self._inputs = {c["case_id"]: c for c in map(json.loads, cases.read_text(encoding="utf-8").splitlines())}
        except Exception:  # noqa: BLE001
            return
        same = lambda k: (old[k].get("status"), old[k].get("value"), old[k].get("error_code")) == (new.get(k, {}).get("status"), new.get(k, {}).get("value"), new.get(k, {}).get("error_code"))  # noqa: E731
        order = sorted(old, key=lambda k: same(k))                       # differences first
        rows = []
        for k in order[:40]:
            c = self._inputs.get(k, {})
            ok = same(k)
            rows.append((f"{'✓' if ok else '✗'} {self._call(c.get('export', name), c.get('input', k))}  old {self._got(old[k])}  new {self._got(new.get(k))}", "green" if ok else "bold red"))
        bad = sum(1 for k in old if not same(k))
        self._show(title, "cyan", rows, "".join("✓" if same(k) else "✗" for k in old),
                   f"{len(old) - bad} of {len(old)} tests give the same answer" + (f", {bad} differ" if bad else ""))

    def say(self, e: dict, kind: str, who: str, text: str) -> None:
        self.feed.append((_ts(e) - self.t0, kind, who, text))

    def _set(self, piece: dict | None, state: str, style: str, note: str | None = None, note_style: str = "dim") -> None:
        if piece:
            piece["state"], piece["style"], piece["touched"] = state, style, self.now
            if note is not None:
                piece["note"], piece["note_style"] = note, note_style

    def _code(self, piece: dict | None, path: str, content: str) -> None:
        if piece and content and content != piece["code"]:
            same = 0                                    # keep what is unchanged on screen, retype only from the first difference
            for a, b in zip(piece["code"], content):
                if a != b:
                    break
                same += 1
            piece["code"], piece["path"], piece["shown"], piece["touched"] = content, path, float(min(same, int(piece["shown"]))), self.now

    def tick(self, dt: float) -> None:
        if self.activity:
            total = max(len(self.activity["rows"]), len(self.activity["marks"]) / 4)
            self.activity["shown"] = min(self.activity["shown"] + dt * 14, total + 1)   # tests appear one after another
        for s in self.pieces.values():
            if s["shown"] < len(s["code"]):
                s["shown"] = min(len(s["code"]), s["shown"] + max(len(s["code"]) / 2.2, 90) * dt)

    @property
    def typing(self) -> bool:
        return any(s["shown"] < len(s["code"]) for s in self.pieces.values())

    # ── events ─────────────────────────────────────────────────────────────
    def apply(self, e: dict) -> None:
        t, p, c = e["type"], e["payload"], e.get("chunk_id") or ""
        self.now = _ts(e)
        piece = self.pieces.get(c)
        name = (piece or {}).get("what") or c          # people read function names, not piece ids
        worker = self.mode[9:] if self.mode.startswith("outside: ") else "worker"
        if t == "run.started":
            self.t0, self.mode = _ts(e), p.get("mode", "team")
            try:
                root = Path(p["profile_dir"])
                lang = json.loads((root / "profile.json").read_text(encoding="utf-8")).get("languages", {})
                self.title = f"{lang.get('source', '?')} → {lang.get('target', '?')}"
                for cid in p.get("chunks", []):
                    m = json.loads((root / "chunks" / f"{cid}.json").read_text(encoding="utf-8"))
                    src_path = m["source_files"][0]
                    src = _only_function((root / src_path).read_text(encoding="utf-8"), m["exports"])
                    self.pieces[cid] = _piece(", ".join(m["exports"]), m.get("depends_on", []), src, src_path)
            except Exception:  # noqa: BLE001
                self.pieces = {cid: _piece() for cid in p.get("chunks", [])}
            self.say(e, "run", "run", f"{len(self.pieces)} functions to rewrite")
        elif t == "plan.accepted":
            names = lambda level: " and ".join(self.pieces[x]["what"] or x for x in level if x in self.pieces)  # noqa: E731
            self.say(e, "planner", "planner", "order: " + ", then ".join(names(level) for level in p["levels"]))
        elif t == "planner.question":
            self.say(e, "planner", "planner", f"asks the expert: {_short(p.get('question'), 110)}")
        elif t == "worker.question":
            if piece:
                piece["asked"] += 1
            self._set(piece, "asking the expert", "yellow", "waiting for the expert's answer…", "yellow")
            self.say(e, "worker", worker, f"asks the expert about {name}: {_short(p.get('question'), 110)}")
        elif t == "tool.probe_source":
            self.say(e, "expert", "expert", f"runs the original to find out ({_probe_count(p)})")
            try:
                rows = [(f"{self._call(p.get('export', ''), r.get('input'), 52)}  →  {self._got(r)}", "yellow") for r in json.loads(p.get("result") or "[]")]
            except Exception:  # noqa: BLE001
                rows = [(_short(p.get("result"), 100), "yellow")]
            self._show("expert  ·  running the original Python to see what it really does", "yellow", rows)
        elif t == "decision.recorded":
            self.rules.append(p)
            self.say(e, "expert", "expert", f"rule: {_short(p['ruling'], 100)}")
        elif t == "decision.rejected":
            self.say(e, "checker", "checker", "refuses the expert's rule: it would change what the code does")
        elif t == "steward.answered":
            if piece and piece["state"] == "asking the expert":
                self._set(piece, "being written", "blue", "got the expert's answer", "yellow")
            if not p.get("decision_id"):
                self.say(e, "expert", "expert", f"answers: {_short(p.get('answer'), 110)}")
        elif t == "expert.hint":
            self.say(e, "expert", "expert", f"reads the error and tells the worker: {_short(p.get('hint'), 110)}")
        elif t == "worker.escalated":
            self.say(e, "checker", "checker", f"{name} ran out of tries: a stronger model takes over ({p.get('model', '').split('/')[-1]})")
        elif t == "steward.consulted":
            self.say(e, "checker", "checker", f"asks the expert why {name} fails")
        elif t == "worker.started":
            if piece:
                piece["tries"] += 1
            self._set(piece, "being written", "blue", "" if piece and piece["tries"] == 1 else None)
            self.say(e, "worker", worker, f"writes {name}" + (f" again (try {piece['tries']})" if piece and piece["tries"] > 1 else ""))
        elif t == "tool.check_compile":
            ok = p.get("result") == "BUILD_OK"
            self._code(piece, p.get("path", ""), p.get("content", ""))
            err = next((l for l in (p.get("error") or "").splitlines() if l.startswith("error")), "")
            self._set(piece, "being written", "blue", "compiles" if ok else f"compiler: {_short(err, 110) or 'does not compile yet'}",
                      "green" if ok else "red")
            if not ok:
                self.say(e, "worker", worker, f"{name} does not compile yet, fixing")
        elif t == "tester.started":
            self._set(piece, "tester is trying to break it", "magenta", "passed the tests; the tester is trying to break it…", "magenta")
            self.say(e, "tester", "tester", f"tries to break {name}")
        elif t == "tool.try_inputs":
            bad = [r for r in p.get("rows", []) if r.get("differs")]
            shown = sorted(p.get("rows", []), key=lambda r: not r.get("differs"))
            self._show(f"tester  ·  trying to break {name} with inputs it made up", "red",
                       [(f"{'✗' if r.get('differs') else '✓'} {self._call(p.get('export', name), r.get('input'))}  old {_short(json.dumps(r.get('original'), ensure_ascii=False), 20)}"
                         f"  new {_short(json.dumps(r.get('new'), ensure_ascii=False), 20)}", "bold red" if r.get("differs") else "green") for r in shown],
                       "".join("✗" if r.get("differs") else "✓" for r in p.get("rows", [])),
                       f"{p.get('tried')} inputs tried, {p.get('differ')} differ")
            call = lambda r: f"{p.get('export')}({', '.join(f'{k}={v}' for k, v in r['input'].items())})" if isinstance(r.get("input"), dict) else json.dumps(r.get("input"))  # noqa: E731
            if bad:
                self.say(e, "tester", "tester", f"✗ broke it: {_short(call(bad[0]), 70)}  old → {_short(bad[0]['original'], 30)}  new → {_short(bad[0]['new'], 30)}"
                         + (f"  (+{len(bad) - 1} more)" if len(bad) > 1 else "") + "  · added to the tests")
            else:
                self.say(e, "tester", "tester", f"{p.get('tried')} new inputs, old and new agree")
        elif t == "tester.finished":
            if not p.get("found"):
                self._set(piece, "survived the tester", "green", "✓ the tester could not break it", "green")
                self.say(e, "tester", "tester", f"✓ could not break {name}")
        elif t == "worker.wrote":
            self._code(piece, p.get("path", ""), p.get("content", ""))
        elif t == "candidate.submitted":
            if ":integrate-" in (e.get("attempt_id") or "") or "-after-tester" in (e.get("attempt_id") or ""):
                return
            self._set(piece, "being checked", "cyan", "comparing old and new on the tests…", "cyan")
        elif t == "candidate.rejected":
            if "compile" in (e.get("attempt_id") or ""):
                return
            why = REASONS.get(p.get("reason"), p.get("reason"))
            if p.get("reason") == "REJECTED_BEHAVIOR":
                self._tests(e, name, f"checker  ·  {name}: old code and new code on the same tests")
            cx = self._counterexample(e, piece["what"] if piece else "")
            first = (p.get("detail") or "").splitlines()[0] if p.get("detail") else ""
            if cx:
                self.caught.append(f"{name}: {cx}")
            self._set(piece, f"REJECTED: {why}", "bold red", f"✗ {cx or _short(first, 110)}", "bold red")
            self.say(e, "checker", "checker", f"✗ {name}: {why}. " + _short(cx or first, 120))
        elif t == "candidate.stale" and e["actor"] == "integrator":
            self._set(piece, "re-checking under the new rule", "bold yellow", "a new rule covers this piece: checking it again", "yellow")
            self.say(e, "checker", "checker", f"a new rule covers {name}: checking it again")
        elif t == "candidate.stale":
            self._set(piece, "redo: the rules changed", "bold yellow", "written before a new rule: sent back", "yellow")
            self.say(e, "checker", "checker", f"{name} was written before a new rule: sent back")
        elif t == "candidate.verified":
            self._set(piece, "passes the tests", "green", f"✓ {_plain_count(p)}", "green")
            self._tests(e, name, f"checker  ·  {name}: old code and new code on the same tests")
            self.say(e, "checker", "checker", f"{name}: {_plain_count(p)}")
        elif t == "chunk.accepted":
            if piece:
                piece["cases"] = f"{p.get('cases', {}).get('passed', '?')}/{p.get('cases', {}).get('expected', '?')}"
            self._set(piece, "KEPT ✓", "bold green", f"✓ same answers as the original on {piece['cases'] if piece else ''} tests", "bold green")
            self.say(e, "checker", "checker", f"✓ {name} kept")
        elif t == "chunk.revalidated":
            self.say(e, "checker", "checker", f"{name} still correct under the new rule")
        elif t == "chunk.resumed":
            self._set(piece, "KEPT ✓ (from before)", "bold green")
            self.say(e, "run", "run", f"{name} was kept in the earlier run, reused")
        elif t == "chunk.blocked" and piece and piece["state"].startswith("REJECTED"):
            pass                                   # keep the failing input on screen
        elif t == "chunk.blocked":
            self._set(piece, "NEEDS A HUMAN", "bold red", _short(p.get("reason") or p.get("detail"), 110), "bold red")
            self.say(e, "run", "run", f"✗ {name} needs a human")
        elif t == "evaluation.locked":
            got, want = p.get("cases", {}).get("passed", 0), p.get("cases", {}).get("expected", 0)
            self.hidden_pass, self.hidden_total = self.hidden_pass + got, self.hidden_total + want
            ok = self.hidden_pass == self.hidden_total
            self.hidden = f"{'PASS' if ok else 'FAIL'} {self.hidden_pass}/{self.hidden_total}"
            if got != want:
                self._set(piece, "FAILS the hidden tests", "bold red", f"✗ hidden tests: only {got} of {want} match the original", "bold red")
            self.say(e, "checker", "checker", f"{'✓' if got == want else '✗'} hidden tests for {name}: {got} of {want} match")
        elif t == "run.finished":
            self.finished = p
            self.say(e, "run", "run", f"done: {len(p.get('accepted', []))} of {len(self.pieces)} kept")

    # ── drawing ────────────────────────────────────────────────────────────
    def _code_panel(self, cid: str, s: dict, src_lines: int, new_lines: int, height: int) -> Panel:
        border = s["style"].replace("bold ", "") if s["state"] != "waiting" else "grey37"
        src = "\n".join(s["src"].splitlines()[:src_lines]) or "(source not available)"
        parts: list = [Text(f"original  {s['src_path']}", style="dim"),
                       Syntax(src, _lexer(s["src_path"]), theme=THEME, background_color="default", word_wrap=True)]
        if s["code"]:
            shown = s["code"][: int(s["shown"])]
            typing = int(s["shown"]) < len(s["code"])
            lines = shown.splitlines() or [""]
            view = "\n".join(lines[-new_lines:] if typing else lines[:new_lines]) + (CURSOR if typing else "")
            parts += [Text(f"rewritten  {s['path']}" + ("   typing…" if typing else ""), style="dim"),
                      Syntax(view, _lexer(s["path"]), theme=THEME, background_color="default", word_wrap=True)]
            if not typing and len(lines) > new_lines:
                parts.append(Text(f"… {len(lines) - new_lines} more lines", style="dim"))
        else:
            parts.append(Text("rewritten  (nothing yet)", style="dim"))
        if s["note"]:
            parts.append(Text(s["note"], style=s["note_style"]))
        return Panel(Group(*parts), title=f"[bold]{s['what'] or cid}[/bold]", title_align="left",
                     subtitle=Text(s["state"], style=s["style"]), subtitle_align="right", border_style=border, height=height, box=box.ROUNDED)

    def _activity_panel(self, height: int) -> Panel | None:
        a = self.activity
        if not a:
            return None
        n = int(a["shown"])
        room = max(height - 4 - (1 if a["marks"] else 0), 2)
        parts: list = []
        if a["marks"]:
            marks = Text()
            for ch in a["marks"][: n * 4]:
                marks.append(ch, style="green" if ch == "✓" else "bold red")
            parts.append(marks)
        parts += [Text(text, style=style, no_wrap=True, overflow="ellipsis") for text, style in a["rows"][:n][:room]]
        done = n >= len(a["rows"]) and n * 4 >= len(a["marks"])
        if a["foot"] and done:
            parts.append(Text(a["foot"], style="bold"))
        return Panel(Group(*parts), title=a["title"], title_align="left", border_style=a["style"], height=height, box=box.ROUNDED)

    def render(self, height: int, width: int) -> Group:
        kept = sum(1 for s in self.pieces.values() if s["state"].startswith("KEPT"))
        who = self.mode[9:] if self.mode.startswith("outside: ") else "agent team"
        head = Text.assemble(("  ≡ ", "bold #00e0c4"), ("parity", "bold"), (f"   {self.title}  ·  {who}  ·  {int(max(self.now - self.t0, 0))}s  ·  ", "dim"),
                             (f"{kept}/{len(self.pieces)} kept", "bold green" if kept == len(self.pieces) and kept else "bold"),
                             (f"  ·  hidden tests {self.hidden}" if self.hidden else "", "bold red" if "FAIL" in self.hidden else "green"))
        rule = self.rules[-1] if self.rules else None
        rules = Text.assemble(("  latest rule  ", "bold yellow"), (_short(rule["ruling"], max(width - 20, 40)), "dim")) if rule else None
        caught = Text.assemble(("  caught ", "bold red"), (f"{len(self.caught)}  ", "red"),
                               (_short(self.caught[-1], max(width - 16, 40)), "red")) if self.caught else None

        # one line per function, then ONE code panel: the function being worked on right now
        marks = {"KEPT": ("✓", "bold green"), "NEED": ("✗", "bold red"), "REJE": ("✗", "bold red"), "FAIL": ("✗", "bold red"), "wait": ("·", "dim")}
        wide = max((len(x["what"] or c) for c, x in self.pieces.items()), default=8) + 2
        rows = []
        for c, x in self.pieces.items():
            mark, style = marks.get(x["state"][:4], ("…", x["style"]))
            rows.append(Text.assemble((f"  {mark} ", style), (f"{(x['what'] or c):<{wide}}", "bold" if mark == "…" else "default"),
                                      (f"{x['state']:<30}", x["style"]), (_short(x["note"], max(width - wide - 38, 20)), x["note_style"])))
        listing = Group(*rows)
        active = max((c for c in self.pieces if self.pieces[c]["code"] or self.pieces[c]["tries"]), key=lambda c: self.pieces[c]["touched"], default=None)
        fixed = 2 + len(rows) + 1 + (1 if rules else 0) + (1 if caught else 0)
        free = max(height - fixed - 1, 10)
        grid, code_h, others = None, 0, None
        if active:
            x = self.pieces[active]
            cap = max(min(int(free * 0.6), 26), 9)
            src_lines = max(min(len(x["src"].splitlines()), (cap - 6) // 3), 2)
            new_lines = max(min(len(x["code"].splitlines()) + 1, cap - 6 - src_lines), 3)
            code_h = src_lines + new_lines + 6
            code = self._code_panel(active, x, src_lines, new_lines, code_h)
            side = self._activity_panel(code_h)
            if side is not None and width >= 110:
                grid = Table.grid(expand=True)
                grid.add_column(ratio=1)
                grid.add_column(ratio=1)
                grid.add_row(code, side)
            elif side is not None:
                extra = min(max(free - code_h - 6, 0), 12)
                grid = Group(code, self._activity_panel(extra)) if extra >= 5 else code
                code_h += extra if extra >= 5 else 0
            else:
                grid = code
        elif self.activity:                                   # nobody is writing yet: the expert is settling the planner's questions
            code_h = max(min(int(free * 0.6), 16), 6)
            grid = self._activity_panel(code_h)

        room = max(free - code_h - 2 - (1 if others else 0), 3)
        lines: list[Text] = []
        for secs, kind, who, text in reversed(self.feed):
            line = Text.assemble((f"  {int(secs):>3}s ", "dim"), (f"{who:<10} ", f"bold {ACTOR_STYLE.get(kind, 'white')}"), text)
            if "✗" in text:
                line.stylize("red", 16)
            elif "✓" in text:
                line.stylize("green", 16)
            need = -(-len(line.plain) // max(width - 4, 20))
            if need > room:
                break
            room -= need
            lines.append(line)
        feed = Group(Text(""), *reversed(lines))
        return Group(*[x for x in (head, Text(""), listing, Text(""), grid, rules, caught, feed) if x is not None])


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
    board, path = Board(run_dir), run_dir / "events.jsonl"
    draw = lambda: board.render(console.size.height, console.size.width)  # noqa: E731
    frame = 0.07
    # Own screen, and a redraw only when something changed: redrawing a tall view in place 15 times a second made
    # the terminal flicker. The last frame is printed normally afterwards so it stays in the scrollback.
    with Live(draw(), console=console, auto_refresh=False, screen=True, vertical_overflow="crop") as live:
        last = [None]

        def show() -> None:
            key = (int(board.now - board.t0), len(board.feed), console.size, tuple((x["state"], x["note"], int(x["shown"])) for x in board.pieces.values()),
                   (board.activity or {}).get("title"), int((board.activity or {}).get("shown", 0)))
            if key != last[0]:
                last[0] = key
                live.update(draw(), refresh=True)

        if replay:
            for i, e in enumerate(events := _read(path)):
                board.apply(e)
                gap = max(min(_ts(events[i + 1]) - _ts(e), 2.5), 0.35) / speed if i + 1 < len(events) else 0.0
                waited = 0.0
                while waited < gap or (board.typing and waited < 4.0):
                    board.tick(frame)
                    show()
                    time.sleep(frame)
                    waited += frame
            board.tick(99)
            show()
            time.sleep(1.0)
        else:
            pos, last_read, idle_after_exit = 0, 0.0, 0
            while True:
                if time.time() - last_read > 0.4:
                    last_read = time.time()
                    if path.exists():
                        with path.open("rb") as f:
                            f.seek(pos)
                            buf = f.read()
                        parts = buf.split(b"\n")
                        pos += len(buf) - len(parts.pop())
                        for raw in parts:
                            try:
                                board.apply(json.loads(raw))
                            except json.JSONDecodeError:
                                pass
                    if proc is not None and proc.poll() is not None:
                        idle_after_exit += 1
                if board.t0 and not board.finished:
                    board.now = time.time()
                board.tick(frame)
                show()
                if (board.finished and not board.typing) or idle_after_exit > 5:
                    break
                time.sleep(frame)
    console.print(draw())
