"""Where things live. ROOT is Parity itself; WORK is where the person using it is working.

Run `parity` inside the Parity repo and WORK is the repo (projects/, runs/ as before). Run it from any other
folder and everything it makes goes into `.parity/` in that folder, next to the code being migrated.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _work() -> Path:
    if os.environ.get("PARITY_WORK"):                      # set once by the first process, inherited by the ones it starts
        return Path(os.environ["PARITY_WORK"])
    here = Path.cwd().resolve()
    work = ROOT if here == ROOT or ROOT in here.parents else here / ".parity"
    os.environ["PARITY_WORK"] = str(work)
    return work


WORK = _work()
RUNS = WORK / "runs"
PROJECTS = WORK / "projects"


def project_dir(what: str | None) -> Path:
    """A project by folder path or by name; with nothing given, the one made most recently here."""
    if what:
        for cand in (Path(what), PROJECTS / what, ROOT / what):
            if (cand / "profile.json").exists():
                return cand.resolve()
        raise SystemExit(f"no project at {what} (looked in {Path(what).resolve()} and {PROJECTS / what})")
    made = sorted((p for p in PROJECTS.glob("*/profile.json")), key=lambda p: -p.stat().st_mtime) if PROJECTS.exists() else []
    if not made:
        raise SystemExit("no project here yet. Make one with: parity new <file.py | folder | module>")
    return made[0].parent.resolve()
