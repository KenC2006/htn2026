"""The interactive console: type `parity` with no arguments.

A thin front end: every command calls the same functions as `python -m parity <command>`.
"""
from __future__ import annotations

import difflib
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import cli

ROOT, RUNS = cli.ROOT, cli.RUNS
STATE = RUNS / ".console.json"
console = Console()

LOGO = r"""
██████╗   █████╗  ██████╗  ██╗ ████████╗ ██╗   ██╗
██╔══██╗ ██╔══██╗ ██╔══██╗ ██║ ╚══██╔══╝ ╚██╗ ██╔╝
██████╔╝ ███████║ ██████╔╝ ██║    ██║     ╚████╔╝
██╔═══╝  ██╔══██║ ██╔══██╗ ██║    ██║      ╚██╔╝
██║      ██║  ██║ ██║  ██║ ██║    ██║       ██║
╚═╝      ╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚═╝    ╚═╝       ╚═╝
""".strip("\n")
SHADES = ["#00ff9c", "#00f0b0", "#00e0c4", "#00cfd8", "#00bdec", "#00aaff"]

COMMANDS = [
    ("/use <folder>", "choose the code to migrate (a project folder)"),
    ("/scan", "show what would be migrated, before spending anything"),
    ("/run", "migrate with the agent team, live"),
    ("/solo", "same job with ONE agent, for comparison"),
    ("/check <folder> [name]", "check a translation written by anyone (another model, a person)"),
    ("/cheat", "demo: a fake that hardcodes the visible test answers"),
    ("/replay [run] [speed]", "play a finished run back"),
    ("/status [run]", "result table of a run"),
    ("/runs", "recent runs"),
    ("/compare [runs…]", "side-by-side numbers (defaults to the last 6 runs)"),
    ("/evaluate [run]", "run the hidden test set once"),
    ("/export [run]", "write the patch, report and receipts"),
    ("/models [workers|expert <id>]", "show or change the models for this session"),
    ("/doctor", "check tools, key and budget"),
    ("/clear", "clear the screen"),
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
        return f"{lang.get('source', '?')} → {lang.get('target', '?')}, {len(chunks)} pieces  ({os.path.relpath(folder, ROOT)})"
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
def banner(state: dict) -> None:
    logo = Text()
    for line, shade in zip(LOGO.splitlines(), SHADES):
        logo.append(line + "\n", style=f"bold {shade}")
    tag = Text.assemble(("old code ", "dim"), ("══ same inputs, same outputs ══", "bold #00e0c4"), (" new code", "dim"))
    sub = Text("AI rewrites your code in a new language. Parity proves it still works the same.", style="italic")
    info = Table.grid(padding=(0, 2))
    info.add_column(style="dim", justify="right")
    info.add_column()
    info.add_row("project", _project_line(Path(state["project"])))
    info.add_row("workers", os.environ.get("MODEL_NAME", "not set"))
    info.add_row("expert", os.environ.get("REVIEWER_MODEL", "not set") + "   [dim](a different model family on purpose)[/dim]")
    info.add_row("budget", _budget())
    info.add_row("last run", state.get("last_run") or "none yet")
    console.print(Panel(Group(Align.center(logo), Align.center(tag), Align.center(sub), Text(""), Align.center(info)),
                        border_style="#00cfd8", padding=(1, 2)))
    console.print("  [bold]/run[/bold] migrate   [bold]/solo[/bold] one agent   [bold]/cheat[/bold] try to fool it   "
                  "[bold]/replay[/bold] play back   [bold]/help[/bold] everything else\n", style="dim")


def show_help() -> None:
    t = Table(box=None, padding=(0, 2), show_header=False)
    t.add_column(no_wrap=True)
    t.add_column()
    for name, what in COMMANDS:
        t.add_row(Text(name, style="bold #00e0c4"), what)
    console.print(Panel(t, title="commands", title_align="left", border_style="grey37"))


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
    elif cmd in ("run", "solo"):
        solo = cmd == "solo" or "--solo" in args
        state["last_run"] = _new_id("solo" if solo else "team")
        _save(state)
        _call(["run", project, "--run-id", state["last_run"]] + (["--solo"] if solo else []))
    elif cmd in ("check", "cheat"):
        folder = "tests/cheat_candidate" if cmd == "cheat" else (args[0] if args else "")
        author = "cheater" if cmd == "cheat" else (args[1] if len(args) > 1 else "outside")
        if not folder or not (ROOT / folder).exists() and not Path(folder).exists():
            console.print("[red]which folder holds the translation?[/red]  example: /check tests/fable_oneshot_candidate fable-one-shot")
        else:
            state["last_run"] = _new_id(author)
            _save(state)
            _call(["check", project, str(ROOT / folder if (ROOT / folder).exists() else folder), "--author", author, "--run-id", state["last_run"]])
            _call(["status", state["last_run"]])
    elif cmd in ("replay", "watch"):
        if last():
            _call(["watch", last()] + (["--replay", "--speed", args[1] if len(args) > 1 else "2"] if cmd == "replay" else []))
    elif cmd in ("status", "evaluate", "export"):
        if last():
            _call([cmd, last()])
        else:
            console.print("no run yet: try /run")
    elif cmd == "runs":
        show_runs()
    elif cmd == "compare":
        ids = args or [r["id"] for r in _run_rows(6)][::-1]
        subprocess.run([sys.executable, str(ROOT / "env" / "compare-runs.py"), *ids], cwd=ROOT)
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
    banner(state)
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
