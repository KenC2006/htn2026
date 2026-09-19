"""Live terminal view of a run, in plain words.  python -m ratchet watch <run_id> [--replay]

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

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

ACTOR_STYLE = {"planner": "magenta", "expert": "yellow", "checker": "cyan", "worker": "blue", "solo": "blue", "run": "white"}
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
        worker = "one agent" if self.mode == "single-agent" else self.mode[9:] if self.mode.startswith("outside: ") else f"worker {c}"
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
            self._set(piece, "asking the expert", "yellow", "waiting for the expert's answer…", "yellow")
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
            if piece and piece["state"] == "asking the expert":
                self._set(piece, "being written", "blue", "got the expert's answer", "yellow")
            if not p.get("decision_id"):
                self.say(e, "expert", "expert", f"answers {p.get('to') or c}: {_short(p.get('answer'), 170)}")
        elif t == "steward.consulted":
            self.say(e, "checker", "checker", f"sends the failure on {c} to the expert")
        elif t == "worker.started":
            if piece:
                piece["tries"] += 1
            self._set(piece, "being written", "blue", "" if piece and piece["tries"] == 1 else None)
            self.say(e, "worker", worker, "starts writing" + (f" (try {piece['tries']})" if piece and piece["tries"] > 1 else ""))
        elif t == "tool.check_compile":
            ok = p.get("result") == "BUILD_OK"
            self._code(piece, p.get("path", ""), p.get("content", ""))
            err = next((l for l in (p.get("error") or "").splitlines() if l.startswith("error")), "")
            self._set(piece, "being written", "blue", "compiles" if ok else f"compiler: {_short(err, 110) or 'does not compile yet'}",
                      "green" if ok else "red")
            self.say(e, "worker", worker, "compiles its code: " + ("OK" if ok else "does not compile yet"))
        elif t == "worker.wrote":
            self._code(piece, p.get("path", ""), p.get("content", ""))
        elif t == "candidate.submitted":
            if ":integrate-" in (e.get("attempt_id") or ""):
                return
            self._set(piece, "being checked", "cyan", "checker is comparing it with the original…", "cyan")
            self.say(e, "worker", worker, f"hands in {', '.join(p.get('files', []))}")
        elif t == "candidate.rejected":
            if "compile" in (e.get("attempt_id") or ""):
                return
            why = REASONS.get(p.get("reason"), p.get("reason"))
            cx = self._counterexample(e, piece["what"] if piece else "")
            first = (p.get("detail") or "").splitlines()[0] if p.get("detail") else ""
            if cx:
                self.caught.append(f"{c}: {cx}")
            self._set(piece, f"REJECTED: {why}", "bold red", f"✗ {cx or _short(first, 110)}", "bold red")
            self.say(e, "checker", "checker", f"✗ REJECTS {c}: {why}. " + (cx or _short(first, 150)))
        elif t == "candidate.stale" and e["actor"] == "integrator":
            self._set(piece, "re-checking under the new rule", "bold yellow", "a new rule covers this piece: checking it again", "yellow")
            self.say(e, "checker", "checker", f"the new rule also covers {c}, which was already kept: checking it again")
        elif t == "candidate.stale":
            self._set(piece, "thrown out (old rule), redo", "bold yellow", "written under an old rule: sent back", "yellow")
            self.say(e, "checker", "checker", f"{c} was written under an old rule: thrown out and sent back")
        elif t == "candidate.verified":
            self._set(piece, "matches, joining the others", "green", f"✓ {p.get('detail')}", "green")
            self.say(e, "checker", "checker", f"{c} matches the original: {p.get('detail')}")
        elif t == "chunk.accepted":
            if piece:
                piece["cases"] = f"{p.get('cases', {}).get('passed', '?')}/{p.get('cases', {}).get('expected', '?')}"
                if not piece["code"]:                  # runs recorded before code was put in the events
                    for f in sorted((self.run_dir / "accepted").rglob("*")):
                        if f.is_file() and piece["what"].split(",")[0] in f.read_text(encoding="utf-8", errors="replace"):
                            self._code(piece, f.name, f.read_text(encoding="utf-8", errors="replace"))
                            break
            self._set(piece, "KEPT ✓", "bold green", f"✓ same output as the original on {piece['cases'] if piece else ''} cases", "bold green")
            self.say(e, "checker", "checker", f"✓ {c} KEPT. {p.get('detail')} with everything kept so far")
        elif t == "chunk.revalidated":
            self.say(e, "checker", "checker", f"{c} re-checked under the new rule: still correct, no rewrite needed")
        elif t == "chunk.resumed":
            self._set(piece, "KEPT ✓ (from before)", "bold green")
            self.say(e, "run", "run", f"{c} already proven in the earlier run, reused")
        elif t == "chunk.blocked":
            self._set(piece, "NEEDS A HUMAN", "bold red", _short(p.get("reason") or p.get("detail"), 110), "bold red")
            self.say(e, "run", "run", f"{c} needs a human: {_short(p.get('reason') or p.get('detail'), 140)}")
        elif t == "evaluation.locked":
            got, want = p.get("cases", {}).get("passed", 0), p.get("cases", {}).get("expected", 0)
            self.hidden_pass, self.hidden_total = self.hidden_pass + got, self.hidden_total + want
            ok = self.hidden_pass == self.hidden_total
            self.hidden = f"{'PASS' if ok else 'FAIL'} {self.hidden_pass}/{self.hidden_total}"
            if got != want:
                self._set(piece, "FAILS the hidden test set", "bold red", f"✗ hidden test set: only {got} of {want} match the original", "bold red")
            self.say(e, "checker", "checker", f"{'✓' if got == want else '✗'} hidden test set (never shown to any agent): {c} matches on {got} of {want}")
        elif t == "run.finished":
            self.finished = p
            self.say(e, "run", "run", f"finished: {len(p.get('accepted', []))}/{len(self.pieces)} pieces kept")

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
        return Panel(Group(*parts), title=f"[bold]{cid}[/bold] {s['what']}", title_align="left",
                     subtitle=Text(s["state"], style=s["style"]), subtitle_align="right", border_style=border, height=height)

    def render(self, height: int, width: int) -> Group:
        kept = sum(1 for s in self.pieces.values() if s["state"].startswith("KEPT"))
        head = Text.assemble(("  RATCHET  ", "bold black on green"), f"  {self.title}   ",
                             ("ONE AGENT" if self.mode == "single-agent" else self.mode.upper() if self.mode.startswith("outside") else "AGENT TEAM", "bold"),
                             f"   run {self.run_id}   {int(max(self.now - self.t0, 0))}s   ",
                             (f"{kept}/{len(self.pieces)} kept", "bold green" if kept == len(self.pieces) and kept else "bold"),
                             (f"   shared rules: {len(self.rules)}", "yellow" if self.rules else "dim"),
                             (f"   hidden test set: {self.hidden}" if self.hidden else ""))
        rule = self.rules[-1] if self.rules else None
        rules = Panel(Text(_short(rule["ruling"], width * 2 - 12), style="yellow"),
                      title=f"latest rule from the expert: {rule['decision_id']}, applies to {', '.join(rule['affected_chunks'])}",
                      title_align="left", border_style="yellow") if rule else None
        caught = Panel(Text("\n".join(self.caught[-3:]), style="bold red"), title=f"wrong answers caught by the checker: {len(self.caught)}",
                       title_align="left", border_style="red") if self.caught else None

        # code panels: as many pieces as fit side by side, the most recently active first
        per_row = max(1, min(len(self.pieces), width // 46, 3))
        order = list(self.pieces)
        if len(order) > per_row:
            order = sorted(order, key=lambda c: -self.pieces[c]["touched"])[:per_row]
            order.sort(key=list(self.pieces).index)
        fixed = 2 + (4 if rules else 0) + (len(self.caught[-3:]) + 2 if caught else 0)
        free = max(height - fixed - 1, 12)
        cap = max(min(int(free * 0.6), 34), 10)
        src_lines = max(min(max((len(self.pieces[c]["src"].splitlines()) for c in order), default=3), (cap - 6) // 2), 2)
        longest = max((len(self.pieces[c]["code"].splitlines()) for c in order), default=0)
        new_lines = max(min(longest + 1, cap - 6 - src_lines), 4)
        code_h = src_lines + new_lines + 6
        grid = Table.grid(expand=True)
        for _ in order:
            grid.add_column(ratio=1)
        if order:
            grid.add_row(*[self._code_panel(c, self.pieces[c], src_lines, new_lines, code_h) for c in order])
        hidden = [c for c in self.pieces if c not in order]
        others = Text("  also: " + "   ".join(f"{c} {self.pieces[c]['state']}" for c in hidden), style="dim") if hidden else None

        room = max(free - code_h - 2 - (1 if others else 0), 3)
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
        feed = Panel(Group(*reversed(lines)), title="what the agents are doing", title_align="left", border_style="grey37")
        return Group(*[x for x in (head, Text(""), grid, others, rules, caught, feed) if x is not None])


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
    with Live(draw(), console=console, refresh_per_second=15, screen=False, transient=False) as live:
        if replay:
            for i, e in enumerate(events := _read(path)):
                board.apply(e)
                gap = max(min(_ts(events[i + 1]) - _ts(e), 2.5), 0.35) / speed if i + 1 < len(events) else 0.0
                waited = 0.0
                while waited < gap or (board.typing and waited < 4.0):
                    board.tick(frame)
                    live.update(draw())
                    time.sleep(frame)
                    waited += frame
            board.tick(99)
            live.update(draw())
            return
        seen, last_read, idle_after_exit = 0, 0.0, 0
        while True:
            if time.time() - last_read > 0.4:
                last_read = time.time()
                events = _read(path)
                for e in events[seen:]:
                    board.apply(e)
                seen = len(events)
                if proc is not None and proc.poll() is not None:
                    idle_after_exit += 1
            if board.t0 and not board.finished:
                board.now = time.time()
            board.tick(frame)
            live.update(draw())
            if (board.finished and not board.typing) or idle_after_exit > 5:
                return
            time.sleep(frame)
