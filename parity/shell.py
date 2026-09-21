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
from .paths import PROJECTS  # noqa: E402


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

MODES = {"python": ("Python → Rust", ".py files, folders and installed modules", (".py",)),
         "c": ("C → Rust", ".c files and folders of them; the original is compiled with gcc", (".c", ".h")),
         "arkts": ("TypeScript → ArkTS", "one .ts file; needs DevEco Studio and a running HarmonyOS emulator", (".ts", ".ets"))}
# TypeScript to ArkTS with no file named works on this prepared project (three functions).
ARKTS_PROJECT = ROOT / "fixtures" / "telemetry-workbench" / "ts-arkts-core"

COMMANDS = [
    ("/mode [python|c|arkts]", "what you are migrating: Python → Rust, C → Rust, or TypeScript → ArkTS (run on the HarmonyOS emulator)"),
    ("/check <file>", "which functions in a .py, .c or .ts file (or a folder of .py or .c) can be migrated, and why not for the rest"),
    ("/migrate <file> [folder]", "migrate it (to Rust, or to ArkTS in that mode). With a folder: start from the translation in it and fix only what fails. "
                                 "A run that was cut off is continued; --fresh starts over"),
    ("/verify <file> [folder] [--attack]", "test a translation of that file that someone else wrote (in <folder>/target; no folder: the Rust Parity wrote): "
                                           "fixed and hidden tests, about a minute. --attack adds the AI tester. "
                                           "A folder instead of a file: every file in it that has tests, its Rust in <folder>/<name>/target"),
    ("/demo [python|c|arkts]", "a one-minute replay that shows a failure being caught and fixed (made by examples/quick/build.py)"),
    ("/runs", "recent runs"),
    ("/status [run | file]", "result of a run; with a file, the newest run on it"),
    ("/export [run | file]", "put the proven new code in this folder, with a report"),
    ("/models", "change which AI models the agents use"),
    ("/doctor", "check the setup: Python, rustc, the key"),
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
        return f"(no project at {folder})"


def choose_mode(state: dict, word: str = "") -> None:
    keys = list(MODES)
    words = {"py": "python", "python": "python", "c": "c", "ts": "arkts", "typescript": "arkts", "arkts": "arkts"}
    if word.lower() in words:
        state["mode"] = words[word.lower()]
    else:
        i = picker.pick(console, "What are you migrating?", [(MODES[k][0], MODES[k][1], True) for k in keys], keys.index(state.get("mode", "python")))
        if i is None:
            return
        state["mode"] = keys[i]
    console.print(Text.assemble(("  mode  ", ACCENT), (MODES[state["mode"]][0], "default")))


def _fits_mode(state: dict, what: str) -> bool:
    """/check and /migrate work on the language of the current mode. Code in the other language is refused, with the way out."""
    from .newproject import is_c
    path = Path(what)
    if not path.exists():
        return True                                   # a module name: Python
    found = "arkts" if path.suffix.lower() in (".ts", ".ets") else "c" if is_c(path) else "python"
    if found == state.get("mode", "python"):
        return True
    console.print(f"  that is {MODES[found][0].split()[0]} code and the mode is {MODES[state.get('mode', 'python')][0]}.  "
                  f"[bold]/mode {found}[/bold] switches it.")
    return False


def _run_rows(limit: int = 10) -> list[dict]:
    dirs = sorted((p for p in RUNS.iterdir() if (p / "events.jsonl").exists()), key=lambda p: -(p / "events.jsonl").stat().st_mtime)[:limit] if RUNS.exists() else []
    return [_run_row(d) for d in dirs]


def _run_row(d: Path) -> dict:
    path = d / "events.jsonl"
    lines = [b for b in path.read_bytes().split(b"\n") if b.strip()]
    start = json.loads(lines[0])["payload"] if lines else {}
    fin = next((e["payload"] for e in (json.loads(b) for b in reversed(lines) if b"run.finished" in b) if e["type"] == "run.finished"), None)
    hidden = [e["payload"]["cases"] for e in (json.loads(b) for b in lines if b"evaluation.locked" in b) if e["type"] == "evaluation.locked"]
    return {"id": d.name, "mode": start.get("mode", "team"), "pieces": len(start.get("chunks", [])),
            "kept": len((fin or {}).get("accepted", [])), "done": bool(fin),
            "hidden": f"{sum(h.get('passed', 0) for h in hidden)}/{sum(h.get('expected', 0) for h in hidden)}" if hidden else "-",
            "age": time.time() - path.stat().st_mtime}


def new_project(state: dict, args: list[str]) -> Path | None:
    """Scan real code, tick the functions to migrate, build the project."""
    from .newproject import create
    from .newproject import scan
    if not args:
        console.print("  what code?  [dim]a .py file, a folder, or a module name, e.g.[/dim]  /migrate humanize.number")
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
    ticked = picker.pick_many(console, "Which functions? Public ones start ticked, helpers starting with _ do not. Greyed out: cannot be migrated, with the reason", options,
                              {i for i, f in enumerate(functions) if f.ok and not f.name.startswith("_")})
    if not ticked:
        return
    name = cli.project_name(args[0])
    try:
        project = create(source, name, [functions[i].name for i in ticked], say=lambda m: console.print(f"  {m}", style="dim", highlight=False))
    except SystemExit as e:
        console.print(f"[red]{e.code}[/red]")
        return
    console.print(Text.assemble(("  ready  ", ACCENT), (_project_line(project), "default")))
    return project


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
        with Live(_logo(0.0), console=console, auto_refresh=False, transient=False) as live:
            for step in range(1, 17):
                live.update(_logo(step / 16), refresh=True)
                time.sleep(0.025)
    else:
        console.print(_logo(), end="")
    console.print(Text.assemble(("  old code ", "dim"), ("≡", "bold #00e0c4"), (" new code", "dim"),
                                ("    AI rewrites it. Parity proves it still works the same.", "italic dim")))
    console.print()
    rows = [("mode", MODES[state.get("mode", "python")][0]),
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


def _lang() -> str:
    """What the new code is called in messages: ArkTS in that mode, Rust otherwise."""
    return "ArkTS" if _load().get("mode") == "arkts" else "Rust"


def _new_id(prefix: str) -> str:
    return datetime.now().strftime(f"{prefix}-%H%M%S")


def _has_rust(project: Path, folder: Path) -> bool:
    return any((folder / w).exists() for c in cli._load_profile(project)[1].values() for w in c["write_allowlist"])


def _team_runs(project: Path):
    """The agent team's runs on this project, newest first: (folder, its events)."""
    if not RUNS.exists():
        return
    for d in sorted((p for p in RUNS.iterdir() if (p / "events.jsonl").exists()), key=lambda p: -(p / "events.jsonl").stat().st_mtime):
        ev = cli._jsonl(d / "events.jsonl")
        start = ev[0]["payload"] if ev else {}
        if Path(start.get("profile_dir") or "").resolve() == project.resolve() and not str(start.get("mode", "")).startswith("outside"):
            yield d, ev                            # a /verify run holds someone else's Rust, not Parity's


def _stopped_run(project: Path) -> str | None:
    """The newest team run on this project, if it never finished and has written nothing for 10 minutes: a closed terminal, a killed process."""
    d, ev = next(_team_runs(project), (None, []))
    if d is None or any(e["type"] == "run.finished" for e in ev) or time.time() - (d / "events.jsonl").stat().st_mtime < 600:
        return None
    return d.name


def _own_rust(project: Path) -> Path | None:
    """The Rust kept by the newest run on this project: runs/<run>/accepted."""
    if not (project / "profile.json").exists():
        return None
    return next((d / "accepted" for d, _ in _team_runs(project) if (d / "accepted").is_dir() and _has_rust(project, d / "accepted")), None)


def _kept_note(project: Path, own: Path) -> str:
    """Said when a run's kept Rust does not cover every function, so the missing ones are no surprise."""
    chunks = cli._load_profile(project)[1].values()
    have = sum(all((own / w).exists() for w in c["write_allowlist"]) for c in chunks)
    if have == len(chunks):
        return ""
    done = _run_row(own.parent)["done"]
    return f", which {'kept' if done else 'did not finish and had kept'} {have} of {len(chunks)} functions.\n  /migrate <file> {_rel(own)} writes the rest"


def verify_one(state: dict, what: str, folder: Path, attack: bool, tag: str = "") -> str | None:
    """Test the Rust in `folder` against the tests built for one file (or for a folder migrated as one project). Returns the run, if one started."""
    project = PROJECTS / cli.project_name(what)
    if not (project / "profile.json").exists():
        console.print(f"  no tests have been built for {what} yet. /migrate {what} builds them.")
        return None
    if not _has_rust(project, folder):
        first = next(w for c in cli._load_profile(project)[1].values() for w in c["write_allowlist"])
        console.print(f"  no {_lang()} for {what} in {folder}. It should hold {first} and the rest.")
        return None
    state["last_run"] = _new_id("outside") + tag
    _save(state)
    _call(["check", str(project), str(folder), "--author", "outside", "--run-id", state["last_run"]] + ([] if attack else ["--no-tester"]))
    return state["last_run"]


def verify_folder(state: dict, source: Path, folder: Path | None, attack: bool) -> None:
    """Every file in `source` that has tests, one after another. The Rust for pricing.py is in <folder>/pricing/target, else in <folder>/target. No folder: the Rust Parity itself wrote for each file."""
    suffix = ".c" if state.get("mode", "python") == "c" else ".py"
    files = {cli.project_name(f.name): f for f in sorted(source.rglob(f"*{suffix}"), key=lambda f: (-len(f.parts), str(f)))}      # same name twice: the shallowest one
    built = [f for name, f in sorted(files.items()) if (PROJECTS / name / "profile.json").exists()]
    if not built:
        console.print(f"  no tests have been built for any {suffix} file in {_rel(source)} yet. /migrate builds them, for a file or for the whole folder.")
        return
    results = []
    for n, file in enumerate(built, 1):
        name = cli.project_name(file.name)
        if folder is None:
            rust = _own_rust(PROJECTS / name)
            if rust is None:
                console.print(Text.assemble((f"  {n}/{len(built)}  ", ACCENT), (_rel(file), "bold"), (f"   no {_lang()} yet: /migrate writes it", "dim")), highlight=False)
                results.append((file, None))
                continue
        else:
            rust = next((d for d in (folder / name, folder / file.stem) if d.is_dir() and _has_rust(PROJECTS / name, d)), folder)
        console.print(Text.assemble((f"  {n}/{len(built)}  ", ACCENT), (_rel(file), "bold"), (f"   {_lang()} in {_rel(rust)}", "dim")), highlight=False)
        results.append((file, verify_one(state, str(file), rust, attack, tag=f"-{n}")))
    console.print()
    for file, run_id in results:
        r = _run_row(RUNS / run_id) if run_id and (RUNS / run_id / "events.jsonl").exists() else None
        ok = bool(r) and r["done"] and r["kept"] == r["pieces"] and len(set(r["hidden"].split("/"))) == 1
        detail = f"{r['kept']}/{r['pieces']} functions kept  ·  hidden tests {r['hidden']}  ·  {r['id']}" if r else "not run"
        console.print(Text.assemble(("  pass  " if ok else "  fail  ", "green" if ok else "bold red"), (f"{_rel(file):<30}  ", "default"), (detail, "dim")), highlight=False)
    skipped = len(files) - len(built)
    if skipped:
        console.print(f"  [dim]{skipped} other {suffix} file{'s' if skipped > 1 else ''} with no tests yet: skipped[/dim]")


def arkts(cmd: str, args: list[str], state: dict) -> None:
    """TypeScript to ArkTS: the same team, checker and hidden tests, on the prepared project. The new code is built with DevEco
    and run on the HarmonyOS emulator, so every command first checks that both are there and says what is missing."""
    if not args or cmd == "check":
        console.print(f"  [dim]no .ts file named: using the prepared project ({_rel(ARKTS_PROJECT)}). Your own code: /{cmd} <file.ts>[/dim]", highlight=False)
    if cmd == "check":
        _call(["scan", str(ARKTS_PROJECT)])
        return
    attack = "--attack" in args
    args = [x for x in args if x not in ("--attack", "--fresh")]
    if cmd == "verify" and not args:
        console.print(f"  which folder holds the ArkTS?  example: /verify {_rel(ROOT / 'tests' / 'arkts_known_good')}")
        return
    state["last_run"] = _new_id("team" if cmd == "migrate" else "outside")
    _save(state)
    if cmd == "migrate":
        _call(["run", str(ARKTS_PROJECT), "--run-id", state["last_run"]] + (["--start-from", args[0]] if args else []))
    else:
        _call(["check", str(ARKTS_PROJECT), args[0], "--author", "outside", "--run-id", state["last_run"]] + ([] if attack else ["--no-tester"]))


def handle(line: str, state: dict) -> bool:
    try:
        words = shlex.split(line, posix=False)
    except ValueError:
        words = line.split()
    cmd, args = words[0].lstrip("/").lower(), [w.strip('"') for w in words[1:]]
    if state.get("mode") == "arkts" and cmd in ("check", "migrate", "verify") and args and Path(args[0]).is_dir():
        inside = sorted(Path(args[0]).glob("*.ts"))       # a folder named in TypeScript mode: the .ts file in it
        if len(inside) == 1:
            args[0] = str(inside[0]).replace(os.sep, "/")
        elif inside and cmd != "verify":
            console.print(f"  TypeScript is migrated one file at a time. In {args[0]}: {', '.join(f.name for f in inside)}")
            return True

    if cmd in ("quit", "exit", "q"):
        return False
    if cmd in ("help", "?"):
        show_help()
    elif cmd in ("mode", "modes"):
        choose_mode(state, " ".join(args))
    elif cmd in ("check", "migrate", "verify") and state.get("mode") == "arkts" and not any(Path(x).suffix.lower() == ".ts" for x in args[:1]):
        arkts(cmd, args, state)                          # no .ts file named: the prepared project
    elif cmd in ("check", "migrate", "verify") and args and not _fits_mode(state, args[0]):
        pass
    elif cmd == "check":
        if args:
            _call(["new", args[0], "--list"])
        else:
            console.print("  which file?  example: /check pricing.py")
    elif cmd == "migrate":
        # one command from a file to proven Rust: build the tests if this file has none yet, then run the team
        fresh = "--fresh" in args
        args = [x for x in args if x != "--fresh"]
        if not args:
            console.print("  which file?  example: /migrate pricing.py")
            return True
        project = PROJECTS / cli.project_name(args[0])
        stopped = _stopped_run(project) if (project / "profile.json").exists() and not fresh and len(args) == 1 else None
        if stopped:                                       # pick up where it was cut off: what was kept stays kept
            console.print(f"  [dim]run {stopped} was stopped before it finished: continuing it  (add --fresh to start over)[/dim]", highlight=False)
            state["last_run"] = stopped
            _save(state)
            _call(["run", str(project), "--run-id", stopped, "--resume"])
            return True
        if (project / "profile.json").exists() and not fresh:
            console.print(f"  [dim]using the tests already built for {args[0]}  (add --fresh to rebuild them)[/dim]")
        else:
            project = new_project(state, args[:1])
            if project is None:
                return True
        state["last_run"] = _new_id("team")
        _save(state)
        _call(["run", str(project), "--run-id", state["last_run"]] + (["--start-from", args[1]] if len(args) > 1 else []))
    elif cmd == "verify":
        attack = "--attack" in args                      # also let the AI tester try to break it: minutes per function
        args = [x for x in args if x != "--attack"]
        if not args:
            console.print(f"  which file, and which folder holds its {_lang()}?  example: /verify pricing.py their-folder   (a folder of files works too)")
            return True
        if len(args) == 1 and Path(args[0]).is_dir() and not (PROJECTS / cli.project_name(args[0]) / "profile.json").exists():
            verify_folder(state, Path(args[0]), None, attack)
            return True
        if len(args) == 1:                               # no folder given: the Rust Parity itself wrote for it, if any
            project = PROJECTS / cli.project_name(args[0])
            own = _own_rust(project)
            if own is None:
                console.print(f"  there is no {_lang()} for {args[0]} yet. [bold]/migrate {args[0]}[/bold] writes it; "
                              f"to test {_lang()} someone else wrote: /verify {args[0]} <folder>" if (project / "profile.json").exists() else
                              f"  no tests and no {_lang()} for {args[0]} yet. [bold]/migrate {args[0]}[/bold] builds the tests and writes the {_lang()}; "
                              f"to test {_lang()} someone else wrote afterwards: /verify {args[0]} <folder>")
                return True
            console.print(f"  [dim]no folder given: testing the {_lang()} from run {own.parent.name}{_kept_note(project, own)}[/dim]", highlight=False)
            args.append(str(own))
        source, folder = Path(args[0]), Path(args[1])
        if source.is_dir() and not (PROJECTS / cli.project_name(args[0]) / "profile.json").exists():
            verify_folder(state, source, folder, attack)      # a folder whose files were migrated one by one
        else:
            verify_one(state, args[0], folder, attack)
    elif cmd in ("status", "export"):
        run_id = args[0] if args else None
        if run_id and not (RUNS / run_id / "events.jsonl").exists():          # a file was named: the newest team run on it
            d, _ = next(_team_runs(PROJECTS / cli.project_name(run_id)), (None, None))
            if d is None:
                console.print(f"  no run called {run_id}, and no run on a file of that name. /runs lists them.")
                return True
            run_id = d.name
            console.print(f"  [dim]newest run on {args[0]}: {run_id}[/dim]", highlight=False)
        if run_id is None:
            rows = _run_rows(12)
            i = picker.pick(console, f"Which run to {cmd}?", [(r["id"], f"{r['mode']}  ·  {r['kept']}/{r['pieces']} kept", True) for r in rows]) if rows else None
            run_id = rows[i]["id"] if i is not None else None
        if run_id:
            _call([cmd, run_id])
    elif cmd == "demo":
        which = (args[0].lower() if args else {"python": "python", "c": "c", "arkts": "arkts"}.get(state.get("mode", "python"), "python"))
        speed = {"python": "3.5", "c": "2", "arkts": "1.8"}.get(which)
        if speed is None or not (RUNS / f"quick-{which}" / "events.jsonl").exists():
            console.print("  /demo python, /demo c or /demo arkts.  They are built once with: python quick/build.py  (see examples/quick)")
        else:
            _call(["watch", f"quick-{which}", "--replay", "--speed", speed])
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
    state = {"mode": "python", **{k: v for k, v in _load().items() if k in ("mode", "last_run")}}
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
