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

from . import cli, picker

ROOT, RUNS = cli.ROOT, cli.RUNS
from .paths import PROJECTS, WORK  # noqa: E402


def _rel(folder: Path) -> str:
    """A path the way the person would type it: from where they are, else from Parity's own folder."""
    folder = Path(folder).resolve()
    for base in (Path.cwd().resolve(), ROOT):
        if folder == base or base in folder.parents:
            return str(folder.relative_to(base)).replace(os.sep, "/") or "."
    return str(folder)
STATE = RUNS / ".console.json"
console = Console()
ACCENT = picker.ACCENT

COMMANDS = [
    ("/new <code>", "start from real code: a .py file, a folder, or a module name like humanize.number"),
    ("/run", "migrate the project with the agent team"),
    ("/check <folder> [name]", "check a translation someone else wrote"),
    ("/runs", "recent runs"),
    ("/status [run]", "result of a run"),
    ("/export [run]", "write the patch, report and receipts"),
    ("/mode", "switch the migration: Python → Rust, C → Rust, TypeScript → ArkTS"),
    ("/use <folder>", "point at a specific project folder"),
    ("/models", "change which AI models the agents use"),
    ("/quit", "leave"),
]


# ── state ───────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(state: dict) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _project_line(folder: Path) -> str:
    try:
        profile, chunks = cli._load_profile(folder)
        lang = profile.get("languages", {})
        return f"{lang.get('source', '?')} → {lang.get('target', '?')}  ·  {len(chunks)} pieces  ·  {_rel(folder)}"
    except Exception:  # noqa: BLE001
        return f"(no project at {folder}; choose one with /use <folder>)"


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


# ── migration modes ─────────────────────────────────────────────────────────
# (source, target, words that select it, tools the pair needs on this machine)
MODES = [("Python", "Rust", ("python", "py", "pyrust"), ("rustc",)),
         ("C", "Rust", ("c", "crust"), ("rustc", "clang")),
         ("TypeScript", "ArkTS", ("typescript", "ts", "arkts", "ark"), ("node",))]


def _projects() -> dict[tuple[str, str], list[Path]]:
    """Every project folder in the repo, grouped by language pair."""
    found: dict[tuple[str, str], list[Path]] = {}
    for pattern in ("tests/*/profile.json", "fixtures/*/profile.json", "fixtures/*/*/profile.json", "projects/*/profile.json"):
        for f in sorted(ROOT.glob(pattern)) + (sorted(WORK.glob(pattern)) if WORK != ROOT else []):
            try:
                lang = json.loads(f.read_text(encoding="utf-8")).get("languages") or {}
            except Exception:  # noqa: BLE001
                continue
            if lang.get("source") and lang.get("target"):
                found.setdefault((lang["source"], lang["target"]), []).append(f.parent)
    return found


def choose_mode(state: dict, word: str = "") -> None:
    import shutil
    projects = _projects()
    pairs = [(s, t, words, tools) for s, t, words, tools in MODES]
    pairs += [(s, t, (), ()) for (s, t) in projects if (s, t) not in {(a, b) for a, b, _, _ in MODES}]
    current = _pair(Path(state["project"]))
    pick = None
    if word:
        w = word.lower().replace("→", "").replace("->", "").replace("-", "").replace(" ", "")
        pick = next((i for i, (s, t, words, _) in enumerate(pairs) if w in words or w == str(i + 1) or w == (s + t).lower()), None)
        if pick is None:
            console.print(f"[red]no mode called {word}[/red]")
    if pick is None:
        options = []
        for src, tgt, _, tools in pairs:
            folders = projects.get((src, tgt), [])
            missing = [x for x in tools if not shutil.which(x)]
            note = ("no project folder yet" if not folders else f"needs {', '.join(missing)} installed" if missing
                    else "ready  ·  " + ", ".join(_rel(f) for f in folders))
            options.append((f"{src} → {tgt}" + ("   (current)" if (src, tgt) == current else ""), note, bool(folders)))
        start = next((i for i, (a, b, _, _) in enumerate(pairs) if (a, b) == current), 0)
        pick = picker.pick(console, "Migration", options, start)
        if pick is None:
            return
    src, tgt, _, _ = pairs[pick]
    folders = projects.get((src, tgt), [])
    if not folders:
        console.print(f"[yellow]{src} → {tgt} has no project folder yet.[/yellow] [dim]Put one under fixtures/ (format: CONTRACTS.md), then /mode again.[/dim]")
        return
    folder = folders[0]
    if len(folders) > 1:
        i = picker.pick(console, "Project", [(_rel(f), "", True) for f in folders])
        if i is None:
            return
        folder = folders[i]
    state["project"] = str(folder)
    console.print(Text.assemble(("  mode  ", "#00e0c4"), (f"{src} → {tgt}", "bold"), (f"  ·  {_project_line(folder).split('  ·  ', 1)[1]}", "default")))


def new_project(state: dict, args: list[str]) -> None:
    """/new <file | folder | module name>: scan real code, tick the functions to migrate, build the project."""
    import re
    from .newproject import create
    from .scan_python import scan
    if not args:
        console.print("  what code?  [dim]a .py file, a folder, or a module name, e.g.[/dim]  /new humanize.number")
        return
    try:
        source = cli.find_source(args[0])
        functions = scan(source)
    except SystemExit as e:
        console.print(f"[red]{e.code}[/red]")
        return
    except SyntaxError as e:
        console.print(f"[red]cannot read that as Python: {e}[/red]")
        return
    if not functions:
        console.print("  no functions found there")
        return
    can = [f for f in functions if f.ok]
    console.print(Text.assemble(("  scanned ", "dim"), (str(source), "default"), (f"   {len(can)} of {len(functions)} functions can be migrated", "bold")), highlight=False)
    options = [(f"{f.name}({', '.join(p.name for p in f.params)})", "" if f.ok else f"cannot: {f.reason}", f.ok) for f in functions]
    ticked = picker.pick_many(console, "Which functions? (ones that cannot be migrated are greyed out, with the reason)", options,
                              {i for i, f in enumerate(functions) if f.ok and not f.name.startswith("_")})
    if not ticked:
        return
    name = re.sub(r"[^a-z0-9]+", "-", args[0].replace("\\", "/").rstrip("/").split("/")[-1].removesuffix(".py").lower()).strip("-")
    try:
        project = create(source, name, [functions[i].name for i in ticked], say=lambda m: console.print(f"  {m}", style="dim", highlight=False))
    except SystemExit as e:
        console.print(f"[red]{e.code}[/red]")
        return
    state["project"] = str(project)
    console.print(Text.assemble(("  mode  ", ACCENT), (_project_line(project), "default")))
    console.print("  [dim]/run to migrate it[/dim]")


MODELS = [("qwen/qwen3-coder-next", "$0.12 in / $0.80 out per million tokens  ·  fast, cheap"),
          ("deepseek/deepseek-v4.1-flash", "$0.15 / $0.60  ·  untested here"),
          ("moonshotai/kimi-k2.7-code", "$0.71 / $3.21  ·  reasoning model, slower"),
          ("anthropic/claude-sonnet-5", "$2 / $10"),
          ("anthropic/claude-opus-5", "$5 / $25"),
          ("anthropic/claude-fable-5.1", "$10 / $50  ·  about 25x the cost of qwen per run")]
ROLES = [("planner and workers", "MODEL_NAME"), ("expert and tester", "REVIEWER_MODEL")]


def choose_model(args: list[str]) -> None:
    words = {"workers": 0, "worker": 0, "planner": 0, "expert": 1, "tester": 1}
    if len(args) == 2 and args[0] in words:                       # /models expert <id>
        os.environ[ROLES[words[args[0]]][1]] = args[1]
    else:
        role = picker.pick(console, "Which agents?", [(name, os.environ.get(var, "not set"), True) for name, var in ROLES])
        if role is None:
            return
        var = ROLES[role][1]
        ids = [m for m, _ in MODELS]
        options = [(m + ("   (current)" if m == os.environ.get(var) else ""), note, True) for m, note in MODELS] + [("another model…", "type an OpenRouter id", True)]
        i = picker.pick(console, f"Model for the {ROLES[role][0]}", options, ids.index(os.environ[var]) if os.environ.get(var) in ids else 0)
        if i is None:
            return
        if i == len(MODELS):
            typed = console.input("[dim]  OpenRouter model id:[/dim] ").strip()
            if not typed:
                return
            os.environ[var] = typed
        else:
            os.environ[var] = ids[i]
    for name, var in ROLES:
        console.print(Text.assemble((f"  {name:<21}", ACCENT), (os.environ.get(var, "not set"), "default")), highlight=False)
    console.print("  [dim]for this session only; the default lives in env/secrets.env[/dim]")


def _pair(folder: Path) -> tuple[str, str]:
    try:
        lang = json.loads((folder / "profile.json").read_text(encoding="utf-8")).get("languages") or {}
        return lang.get("source", "?"), lang.get("target", "?")
    except Exception:  # noqa: BLE001
        return "?", "?"


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
    rows = [("mode", _project_line(Path(state["project"]))),
            ("team", "planner  ·  workers in parallel  ·  expert  ·  tester"),
            ("models", f"{short(os.environ.get('MODEL_NAME'))}  +  {short(os.environ.get('REVIEWER_MODEL'))}")]
    for label, value in rows:
        console.print(Text.assemble((f"  {label:<9}", "#00e0c4"), (value, "default")), highlight=False)
    console.print()
    console.print(Text("  /help for commands", style="dim"))
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
    elif cmd == "new":
        new_project(state, args)
    elif cmd in ("mode", "modes"):
        choose_mode(state, " ".join(args))
    elif cmd == "use":
        places = [Path(args[0]), PROJECTS / args[0], ROOT / args[0]] if args else []
        folder = next((f.resolve() for f in places if (f / "profile.json").exists()), Path("."))
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
        run_id = args[0] if args else None
        if run_id is None:
            rows = _run_rows(12)
            i = picker.pick(console, f"Which run to {cmd}?", [(r["id"], f"{r['mode']}  ·  {r['kept']}/{r['pieces']} kept", True) for r in rows]) if rows else None
            run_id = rows[i]["id"] if i is not None else None
        if run_id:
            _call([cmd, run_id])
    elif cmd == "runs":
        show_runs()
    elif cmd in ("models", "model"):
        choose_model(args)
    elif cmd == "doctor":
        _call(["doctor"])
    else:
        names = [c.split()[0].lstrip("/") for c, _ in COMMANDS]
        close = difflib.get_close_matches(cmd, names, n=1)
        console.print(f"[red]unknown command: {cmd}[/red]" + (f"   did you mean [bold]/{close[0]}[/bold]?" if close else "   try /help"))
    return True


def shell() -> int:
    made = sorted(PROJECTS.glob("*/profile.json"), key=lambda f: -f.stat().st_mtime) if WORK != ROOT and PROJECTS.exists() else []
    state = {"project": str(made[0].parent if made else ROOT / "tests" / "flow_fixture"), **_load()}
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
