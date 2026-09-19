"""The interactive console: type `parity` with no arguments.

A thin front end: every command calls the same functions as `python -m parity <command>`.
"""
from __future__ import annotations

import difflib
import json
import os
import shlex
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import cli

ROOT, RUNS = cli.ROOT, cli.RUNS
STATE = RUNS / ".console.json"
console = Console()

COMMANDS = [
    ("/run", "migrate the project with the agent team"),
    ("/check <folder> [name]", "check a translation someone else wrote"),
    ("/runs", "recent runs"),
    ("/status [run]", "result of a run"),
    ("/export [run]", "write the patch, report and receipts"),
    ("/use <folder>", "choose the project to migrate"),
    ("/models [workers|expert <id>]", "show or change the models"),
    ("/quit", "leave"),
]


# ── state ───────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(state: dict) -> None:
    RUNS.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _project_line(folder: Path) -> str:
    try:
        profile, chunks = cli._load_profile(folder)
        lang = profile.get("languages", {})
        return f"{lang.get('source', '?')} → {lang.get('target', '?')}  ·  {len(chunks)} pieces  ·  {os.path.relpath(folder, ROOT).replace(os.sep, '/')}"
    except Exception:  # noqa: BLE001
        return f"(no project at {folder}; choose one with /use <folder>)"


def _budget() -> str:
    try:
        import httpx
        d = httpx.get(os.environ["API_BASE"].rstrip("/") + "/key", headers={"Authorization": f"Bearer {os.environ['API_KEY']}"}, timeout=4).json()["data"]
        return f"${d['usage']:.2f} used of ${d['limit']}"
    except Exception:  # noqa: BLE001
        return "unknown (run /doctor)"


def _run_rows(limit: int = 10) -> list[dict]:
    rows = []
    for d in sorted((p for p in RUNS.iterdir() if (p / "events.jsonl").exists()), key=lambda p: -(p / "events.jsonl").stat().st_mtime)[:limit] if RUNS.exists() else []:
        ev = cli._jsonl(d / "events.jsonl")
        start = ev[0]["payload"] if ev else {}
        fin = next((e["payload"] for e in reversed(ev) if e["type"] == "run.finished"), None)
        hidden = [e["payload"]["cases"] for e in ev if e["type"] == "evaluation.locked"]
        rows.append({"id": d.name, "mode": start.get("mode", "team"), "pieces": len(start.get("chunks", [])),
                     "kept": len((fin or {}).get("accepted", [])), "done": bool(fin),
                     "hidden": f"{sum(h.get('passed', 0) for h in hidden)}/{sum(h.get('expected', 0) for h in hidden)}" if hidden else "-",
                     "age": time.time() - (d / "events.jsonl").stat().st_mtime})
    return rows


# ── drawing ─────────────────────────────────────────────────────────────────
LOGO = """\
██████╗   █████╗  ██████╗  ██╗ ████████╗ ██╗   ██╗
██╔══██╗ ██╔══██╗ ██╔══██╗ ██║ ╚══██╔══╝ ╚██╗ ██╔╝
██████╔╝ ███████║ ██████╔╝ ██║    ██║     ╚████╔╝
██╔═══╝  ██╔══██║ ██╔══██╗ ██║    ██║      ╚██╔╝
██║      ██║  ██║ ██║  ██║ ██║    ██║       ██║
╚═╝      ╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚═╝    ╚═╝       ╚═╝""".splitlines()


def _shade(t: float) -> str:
    """Green to blue, left to right."""
    a, b = (0x00, 0xFF, 0x9C), (0x3D, 0x8B, 0xFF)
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _logo(lit: float = 1.0) -> Text:
    """The wordmark with a left-to-right gradient; `lit` < 1 draws only the left part bright (used for the start-up sweep)."""
    width = max(map(len, LOGO))
    out = Text()
    for line in LOGO:
        out.append("  ")
        for i, ch in enumerate(line):
            t = i / width
            out.append(ch, style=f"bold {_shade(t)}" if t <= lit else "grey23")
        out.append("\n")
    return out


def banner(state: dict, animate: bool = False) -> None:
    short = lambda m: (m or "not set").split("/")[-1]  # noqa: E731
    console.print()
    if animate and console.is_terminal:
        from rich.live import Live
        with Live(_logo(0.0), console=console, refresh_per_second=30, transient=False) as live:
            for step in range(1, 17):
                live.update(_logo(step / 16))
                time.sleep(0.025)
    else:
        console.print(_logo(), end="")
    console.print(Text.assemble(("  old code ", "dim"), ("≡", "bold #00e0c4"), (" new code", "dim"),
                                ("    AI rewrites it. Parity proves it still works the same.", "italic dim")))
    console.print()
    rows = [("project", _project_line(Path(state["project"]))),
            ("team", "planner  ·  workers in parallel  ·  expert  ·  tester"),
            ("models", f"{short(os.environ.get('MODEL_NAME'))}  +  {short(os.environ.get('REVIEWER_MODEL'))}"),
            ("budget", _budget())]
    for label, value in rows:
        console.print(Text.assemble((f"  {label:<9}", "#00e0c4"), (value, "default")), highlight=False)
    console.print()
    hint = Text("  ")
    for name, what in (("/run", "migrate"), ("/check", "test someone else's translation"), ("/runs", "history"), ("/help", "more")):
        hint.append(name, style="bold").append(f" {what}     ", style="dim")
    console.print(hint)
    console.print()


def show_help() -> None:
    t = Table(box=None, padding=(0, 2), show_header=False, pad_edge=True)
    t.add_column(no_wrap=True)
    t.add_column()
    for name, what in COMMANDS:
        t.add_row(Text(name, style="bold #00e0c4"), what)
    console.print(t)


def show_runs() -> None:
    t = Table(box=None, padding=(0, 2), header_style="dim")
    for col in ("run", "who", "pieces kept", "hidden test set", "when"):
        t.add_column(col)
    for r in _run_rows():
        age = r["age"]
        when = f"{int(age // 60)} min ago" if age < 3600 else f"{int(age // 3600)} h ago" if age < 86400 else f"{int(age // 86400)} d ago"
        kept = Text(f"{r['kept']}/{r['pieces']}" + ("" if r["done"] else "  (not finished)"),
                    style="green" if r["done"] and r["kept"] == r["pieces"] else "yellow")
        bad = r["hidden"] != "-" and len(set(r["hidden"].split("/"))) > 1
        t.add_row(r["id"], r["mode"], kept, Text(r["hidden"], style="bold red" if bad else "green" if r["hidden"] != "-" else "dim"), when)
    console.print(t)


# ── commands ────────────────────────────────────────────────────────────────
def _call(argv: list[str]) -> int:
    try:
        return cli.main(argv)
    except SystemExit as e:                     # argparse and sys.exit("message") inside commands
        if e.code not in (0, None):
            console.print(f"[red]{e.code}[/red]" if isinstance(e.code, str) else "")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]stopped[/yellow]")
        return 130


def _new_id(prefix: str) -> str:
    return datetime.now().strftime(f"{prefix}-%H%M%S")


def handle(line: str, state: dict) -> bool:
    try:
        words = shlex.split(line, posix=False)
    except ValueError:
        words = line.split()
    cmd, args = words[0].lstrip("/").lower(), [w.strip('"') for w in words[1:]]
    project = state["project"]
    last = lambda: args[0] if args else state.get("last_run")  # noqa: E731

    if cmd in ("quit", "exit", "q"):
        return False
    if cmd in ("help", "?"):
        show_help()
    elif cmd == "clear":
        console.clear()
        banner(state)
    elif cmd == "use":
        folder = (Path(args[0]) if args and Path(args[0]).is_absolute() else ROOT / (args[0] if args else "")).resolve()
        if args and (folder / "profile.json").exists():
            state["project"] = str(folder)
            console.print(f"project: {_project_line(folder)}")
        else:
            console.print("[red]that folder has no profile.json[/red]  example: /use tests/flow_fixture")
    elif cmd == "scan":
        _call(["scan", project])
    elif cmd == "run":
        state["last_run"] = _new_id("team")
        _save(state)
        _call(["run", project, "--run-id", state["last_run"]])
    elif cmd == "check":
        folder = args[0] if args else ""
        author = args[1] if len(args) > 1 else "outside"
        if not folder or not (ROOT / folder).exists() and not Path(folder).exists():
            console.print("[red]which folder holds the translation?[/red]  example: /check tests/fable_oneshot_candidate fable-one-shot")
        else:
            state["last_run"] = _new_id(author)
            _save(state)
            _call(["check", project, str(ROOT / folder if (ROOT / folder).exists() else folder), "--author", author, "--run-id", state["last_run"]])
            _call(["status", state["last_run"]])
    elif cmd in ("status", "evaluate", "export"):
        if last():
            _call([cmd, last()])
        else:
            console.print("no run yet: try /run")
    elif cmd == "runs":
        show_runs()
    elif cmd in ("models", "model"):
        keys = {"workers": "MODEL_NAME", "worker": "MODEL_NAME", "expert": "REVIEWER_MODEL"}
        if len(args) == 2 and args[0] in keys:
            os.environ[keys[args[0]]] = args[1]
        console.print(f"workers  {os.environ.get('MODEL_NAME')}\nexpert   {os.environ.get('REVIEWER_MODEL')}\n"
                      "[dim]change for this session: /models workers <openrouter id>   /models expert <openrouter id>[/dim]")
    elif cmd == "doctor":
        _call(["doctor"])
    else:
        names = [c.split()[0].lstrip("/") for c, _ in COMMANDS]
        close = difflib.get_close_matches(cmd, names, n=1)
        console.print(f"[red]unknown command: {cmd}[/red]" + (f"   did you mean [bold]/{close[0]}[/bold]?" if close else "   try /help"))
    return True


def shell() -> int:
    state = {"project": str(ROOT / "tests" / "flow_fixture"), **_load()}
    if not (Path(state["project"]) / "profile.json").exists():
        state["project"] = str(ROOT / "tests" / "flow_fixture")
    console.clear()
    banner(state, animate=True)
    while True:
        try:
            line = console.input("[bold #00e0c4]parity ›[/bold #00e0c4] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not line:
            continue
        if not handle(line, state):
            break
        _save(state)
        console.print()
    console.print("[dim]bye[/dim]")
    return 0
