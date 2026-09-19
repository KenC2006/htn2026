"""python -m parity ...   Works from any shell: switches to the project's own Python and loads env/secrets.env itself."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_python() -> Path | None:
    for rel in (".venv-swarm/Scripts/python.exe", ".venv-swarm/bin/python"):
        if (ROOT / rel).exists():
            return ROOT / rel
    return None


def _load_env() -> None:
    os.environ.setdefault("JIUWENSWARM_HOME", str(ROOT))      # keep WorkSwarm state inside the repo
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    secrets = ROOT / "env" / "secrets.env"
    if secrets.exists():
        for line in secrets.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_env()
py = _project_python()
if py and Path(sys.prefix).resolve() != py.parents[1].resolve():
    sys.exit(subprocess.call([str(py), "-m", "parity", *sys.argv[1:]], cwd=os.getcwd()))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from .cli import main  # noqa: E402

sys.exit(main())
