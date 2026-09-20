"""Static scan of a TypeScript file: which top-level `function` declarations could be migrated
to ArkTS, and the code each one needs. Thin wrapper around `ts_arkts/scan.cjs`, which does the
actual parsing with the TypeScript compiler API (the same pinned compiler DevEco uses to build
ArkTS). Mirrors `scan_python.py`'s shape exactly: a `Function` dataclass with `.ok`/`.reason`,
`scan_file`/`scan`, so `newproject.py`'s language-agnostic helpers (`rules_for`, `default_rule`,
`make_value`, `make_cases`, `suggest`) work unchanged against either language's functions.

See docs/TS_ONBOARDING_PLAN.md for scope and the purity/support rules `scan.cjs` enforces.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCANNER = ROOT / "ts_arkts" / "scan.cjs"


def _home() -> Path:
    return Path(os.environ.get("PARITY_DEVECO_HOME", r"C:\Program Files\Huawei\DevEco Studio"))


def node_and_ts() -> tuple[Path, Path]:
    """The pinned Node + TypeScript compiler DevEco ships, or overrides for a non-Windows/CI box."""
    home = _home()
    node = Path(os.environ.get("PARITY_NODE", str(home / "tools/node/node.exe")))
    ts = Path(os.environ.get("PARITY_TYPESCRIPT", str(home / "tools/ohpm/node_modules/typescript/lib/typescript.js")))
    return node, ts


_DOMAIN_TYPE = {"number": "float", "string": "str", "boolean": "bool"}   # TS type -> parity/engine/domain.py vocabulary


def _domain_type(ts_kind: str | None) -> str | None:
    """`newproject.py`'s helpers (rules_for, default_rule, make_value, in_domain) speak the same
    `int`/`float`/`str`/`bool`/`list[...]` vocabulary scan_python.py already reports for Python type
    hints. TypeScript's `number` has no int/float split, so it maps to `float`, a safe superset;
    `newproject_ts.arkts_type` maps back to ArkTS types when writing the generated ArkTS source."""
    if ts_kind is None:
        return None
    if ts_kind.endswith("[]"):
        inner = _domain_type(ts_kind[:-2])
        return f"list[{inner}]" if inner else None
    return _DOMAIN_TYPE.get(ts_kind)


@dataclass
class Param:
    name: str
    type: str | None            # domain vocabulary: "float" | "str" | "bool" | "list[float]" | ... ; None = not one we support
    optional: bool = False      # undefined / null / `?` is an accepted value
    has_default: bool = False


@dataclass
class Function:
    name: str
    file: Path
    source: str
    params: list[Param] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)     # other top-level functions of this file it calls (transitively needed)
    needs: list[str] = field(default_factory=list)     # source of helper functions it relies on
    reason: str = ""                                    # why it cannot be migrated ('' = it can)
    defaults: dict = field(default_factory=dict)        # param name -> literal default value, read by newproject._default_value

    @property
    def ok(self) -> bool:
        return not self.reason

    @property
    def module(self) -> str:
        """Kept for parity with scan_python.py's Function; TS has no dotted module name, just a file stem."""
        return self.file.stem


def scan_file(file: Path) -> list[Function]:
    node, ts = node_and_ts()
    if not node.is_file():
        raise RuntimeError(f"Node not found at {node}. Install DevEco Studio or set PARITY_NODE.")
    if not ts.is_file():
        raise RuntimeError(f"TypeScript compiler not found at {ts}. Install DevEco Studio or set PARITY_TYPESCRIPT.")
    run = subprocess.run([str(node), str(SCANNER), str(ts), str(file)], capture_output=True, text=True, timeout=60)
    if run.returncode != 0:
        raise RuntimeError(f"TypeScript scan of {file} failed:\n{(run.stderr or run.stdout)[-2000:]}")
    data = json.loads(run.stdout)
    out = []
    for f in data["functions"]:
        params = [Param(p["name"], _domain_type(p["type"]), p["optional"], p["hasDefault"]) for p in f["params"]]
        fn = Function(name=f["name"], file=Path(file), source=f["source"], params=params,
                      calls=f["calls"], needs=f["needs"], reason=f.get("reason") or "")
        fn.defaults = {p["name"]: p["default"] for p in f["params"] if p["hasDefault"]}
        out.append(fn)
    return out


def scan(path: Path) -> list[Function]:
    """A .ts file, or a folder (every .ts file in it, not recursing into tests). Packages/imports
    across files are not supported yet (docs/TS_ONBOARDING_PLAN.md); each file is scanned on its own."""
    path = Path(path)
    if path.is_file():
        return scan_file(path)
    found = []
    for file in sorted(path.rglob("*.ts")):
        name = file.name
        if any(part in ("tests", "test", "node_modules", "__pycache__") for part in file.parts) or name.endswith(".d.ts") or name.endswith(".test.ts"):
            continue
        found += scan_file(file)
    return found
