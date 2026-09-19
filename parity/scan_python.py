"""Static scan of a Python module: which top-level functions could be migrated, and the code each one needs.

Plain code, no AI. A function qualifies when data goes in and data comes out: the checker works by comparing
outputs, so anything that touches files, the network, the clock, randomness or hidden state is refused, with the reason.
Types and input ranges are settled later (discover.py) by actually running the original.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

# names that make a function's result depend on something other than its inputs
IMPURE_CALLS = {"open", "print", "input", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "breakpoint", "exit", "quit"}
IMPURE_MODULES = {"os", "sys", "io", "time", "random", "secrets", "socket", "subprocess", "requests", "urllib", "http", "pathlib", "shutil",
                  "logging", "threading", "multiprocessing", "asyncio", "locale", "gettext", "tempfile", "glob", "sqlite3", "pickle",
                  "ctypes", "signal", "uuid", "getpass", "platform", "warnings", "importlib", "inspect"}
IMPURE_ATTRS = {("datetime", "now"), ("datetime", "today"), ("datetime", "utcnow"), ("date", "today")}
MUTATORS = {"append", "extend", "insert", "pop", "remove", "clear", "update", "setdefault", "add", "discard", "sort", "reverse", "popitem"}
SCALARS = {"int", "float", "str", "bool"}


@dataclass
class Param:
    name: str
    type: str | None            # "int" | "float" | "str" | "bool" | "list[int]" ... ; None = not known from the hints
    optional: bool = False      # None is an accepted value
    has_default: bool = False


@dataclass
class Function:
    name: str
    module: str                 # dotted module name
    file: Path
    lineno: int
    source: str
    params: list[Param] = field(default_factory=list)
    returns_hint: str | None = None
    returns_tuple: bool = False
    calls: list[str] = field(default_factory=list)       # other top-level functions of this module it calls (transitively needed)
    needs: list[str] = field(default_factory=list)       # source snippets it relies on: helpers, constants, imports
    reason: str = ""                                      # why it cannot be migrated ('' = it can)

    @property
    def ok(self) -> bool:
        return not self.reason


def _hint(node: ast.expr | None) -> tuple[str | None, bool]:
    """(type, optional) from an annotation, or (None, False) when it is missing or not one we support."""
    if node is None:
        return None, False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None, False
    if isinstance(node, ast.Name):
        return (node.id, False) if node.id in SCALARS else (None, False)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):          # X | None, int | float
        sides = [node.left, node.right]
        if any(isinstance(s, ast.Constant) and s.value is None for s in sides):
            other = next(s for s in sides if not (isinstance(s, ast.Constant) and s.value is None))
            return _hint(other)[0], True
        kinds = {_hint(s)[0] for s in sides}
        return ("float", False) if kinds == {"int", "float"} else (None, False)
    if isinstance(node, ast.Subscript):
        base = node.value.attr if isinstance(node.value, ast.Attribute) else getattr(node.value, "id", "")
        if base == "Optional":
            return _hint(node.slice)[0], True
        if base in ("list", "List", "Sequence", "Iterable"):
            inner = _hint(node.slice)[0]
            return (f"list[{inner}]", False) if inner in SCALARS else (None, False)
    return None, False


MEMO_DECORATORS = {"lru_cache", "cache"}   # functools: remembers answers, never changes them


def _is_memo(decorator: ast.AST) -> bool:
    d = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", "")) in MEMO_DECORATORS


def _filled_once(fn: ast.AST) -> set[str]:
    """Globals this function only ever sets inside `if NAME is None:`: a table built on first use.
    That is a cache, not state: every call sees the same table, so calls cannot affect each other."""
    declared = {n for sub in ast.walk(fn) if isinstance(sub, ast.Global) for n in sub.names}
    guarded: dict[str, set[int]] = {n: set() for n in declared}
    for sub in ast.walk(fn):
        t = sub.test if isinstance(sub, ast.If) else None
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id in declared and len(t.ops) == 1
                and isinstance(t.ops[0], ast.Is) and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value is None):
            for inner in sub.body:
                guarded[t.left.id] |= {id(x) for x in ast.walk(inner)}
    stores = [x for x in ast.walk(fn) if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)) and x.id in declared]
    return {n for n in declared if all(id(x) in guarded[n] for x in stores if x.id == n)}


class _Uses(ast.NodeVisitor):
    def __init__(self, caches: set[str] = frozenset()) -> None:
        self.names: set[str] = set()
        self.problems: list[str] = []
        self.caches = caches

    def visit_Name(self, node: ast.Name) -> None:
        self.names.add(node.id)

    def visit_Global(self, node: ast.Global) -> None:
        names = [n for n in node.names if n not in self.caches]
        if names:
            self.problems.append(f"changes global state ({', '.join(names)}) on line {node.lineno}")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.problems.append(f"changes outer state on line {node.lineno}")

    def visit_Yield(self, node: ast.Yield) -> None:
        self.problems.append("is a generator")

    visit_YieldFrom = visit_Yield

    def visit_Await(self, node: ast.Await) -> None:
        self.problems.append("is async")

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in IMPURE_CALLS:
            self.problems.append(f"calls {node.func.id}() on line {node.lineno}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            base, attr = node.value.id, node.attr
            if (base, attr) in IMPURE_ATTRS:
                self.problems.append(f"reads the clock ({base}.{attr}) on line {node.lineno}")
        self.generic_visit(node)


def _module_name(file: Path) -> tuple[str, Path]:
    """Dotted module name and the folder that must be on sys.path to import it."""
    parts, folder = [file.stem], file.parent
    while (folder / "__init__.py").exists():
        parts.insert(0, folder.name)
        folder = folder.parent
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts), folder


class _Module:
    """What one source file defines at the top level."""

    def __init__(self, file: Path) -> None:
        self.file = file
        text = file.read_text(encoding="utf-8")
        self.tree, self.lines = ast.parse(text), text.splitlines()
        self.name, _ = _module_name(file)
        self.functions: dict[str, ast.FunctionDef] = {}
        self.constants: dict[str, list[ast.stmt]] = {}
        self.state: dict[str, int] = {}                        # names some function rebinds with `global`
        self.imported: dict[str, tuple[str, str, str]] = {}    # local name -> (module, original name, statement text)
        self.setup: list[ast.Expr] = []                        # module-level calls such as _register(...) that fill tables at import
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    mod = (("." * node.level) + (node.module or "")) if isinstance(node, ast.ImportFrom) else a.name
                    self.imported[(a.asname or a.name).split(".")[0]] = (mod, a.name, self.seg(node))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and getattr(node, "value", None) is not None:
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(t, ast.Name):
                        self.constants.setdefault(t.id, []).append(node)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                self.setup.append(node)
            elif isinstance(node, (ast.If, ast.Try)):              # "if TYPE_CHECKING:" imports and similar
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        for a in sub.names:
                            self.imported.setdefault((a.asname or a.name).split(".")[0], ("typing-only", a.name, ""))
        for fn in self.functions.values():
            caches = _filled_once(fn)
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Global):
                    for n in sub.names:
                        if n not in caches:
                            self.state[n] = sub.lineno

    def seg(self, n: ast.AST) -> str:
        first = n.decorator_list[0].lineno if getattr(n, "decorator_list", None) else n.lineno
        return "\n".join(self.lines[first - 1: n.end_lineno])

    def resolve(self, mod: str) -> Path | None:
        """The file a relative import points at."""
        if not mod.startswith("."):
            return None
        folder = self.file.parent
        for _ in range(len(mod) - len(mod.lstrip(".")) - 1):
            folder = folder.parent
        rest = mod.lstrip(".")
        base = folder.joinpath(*rest.split(".")) if rest else folder
        for cand in (base.with_suffix(".py"), base / "__init__.py"):
            if cand.exists():
                return cand
        return None


_MODULES: dict[Path, _Module] = {}


def _module(file: Path) -> _Module:
    file = file.resolve()
    if file not in _MODULES:
        _MODULES[file] = _Module(file)
    return _MODULES[file]


def _closure(m: _Module, name: str, seen: set) -> tuple[list[str], list[str], list[str]]:
    """(problems, helper functions of the same file, source snippets needed) for one function, following what it calls."""
    node = m.functions[name]
    uses = _Uses(_filled_once(node))
    for part in node.body + node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
        uses.visit(part)
    problems, helpers, snippets = list(uses.problems), [], []
    local = {a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs}
    for sub in ast.walk(node):                                   # writes to module-level tables or objects
        target = None
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in MUTATORS:
            target = sub.func.value
        elif isinstance(sub, (ast.Subscript, ast.Attribute)) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            target = sub.value
        if isinstance(target, ast.Name) and target.id in m.constants and target.id not in local:
            problems.append(f"changes the module-level `{target.id}` on line {sub.lineno}, so calls affect each other")
    for used in sorted(uses.names - local):
        if used in m.state:
            problems.append(f"reads `{used}`, which other code changes while the program runs (global on line {m.state[used]})")
        elif used in m.functions and used != name:
            helpers.append(used)
            if (m.file, used) not in seen:
                seen.add((m.file, used))
                p, h, s = _closure(m, used, seen)
                problems += [x if x.startswith("calls ") else f"calls {used}(), which {x}" for x in p]
                helpers += h
                snippets += s + [m.seg(m.functions[used])]
        elif used in m.constants:
            snippets += [m.seg(n) for n in m.constants[used]]
            for call in m.setup:                                   # tables filled at import time, e.g. _irregular("person", "people")
                f = call.value.func
                if isinstance(f, ast.Name) and f.id in m.functions and used in {n.id for n in ast.walk(m.functions[f.id]) if isinstance(n, ast.Name)}:
                    snippets += [m.seg(m.functions[f.id]), m.seg(call)]
        elif used in m.imported:
            mod, original, stmt = m.imported[used]
            root = mod.lstrip(".").split(".")[0]
            if mod.startswith("."):
                target = m.resolve(mod)
                other = _module(target) if target else None
                if other is None or (original not in other.functions and original not in other.constants):
                    problems.append(f"uses `{used}` from {mod}, whose source I cannot read")
                elif original in other.functions and (other.file, original) not in seen:
                    seen.add((other.file, original))
                    p, _, s = _closure(other, original, seen)
                    problems += [x if x.startswith("calls ") else f"calls {original}() from {other.name}, which {x}" for x in p]
                    snippets += s + [f"# from {other.name}\n" + other.seg(other.functions[original])]
                elif original in other.constants:
                    snippets += [f"# from {other.name}\n" + other.seg(n) for n in other.constants[original]]
            elif root in IMPURE_MODULES:
                problems.append(f"uses the `{root}` module (files, network, clock, randomness or process state)")
            elif stmt:
                snippets.insert(0, stmt)
    return problems, helpers, snippets


def scan_file(file: Path) -> list[Function]:
    m = _module(Path(file))
    out = []
    for name, node in m.functions.items():
        f = Function(name=name, module=m.name, file=m.file, lineno=node.lineno, source=m.seg(node))
        a = node.args
        if isinstance(node, ast.AsyncFunctionDef):
            f.reason = "is async"
        elif a.vararg or a.kwarg:
            f.reason = "takes *args or **kwargs"
        elif not all(_is_memo(d) for d in node.decorator_list):
            f.reason = f"has a decorator (@{ast.unparse(node.decorator_list[0])}), so its real behavior is defined elsewhere"
        elif not (a.args or a.kwonlyargs or a.posonlyargs):
            f.reason = "takes no inputs"
        if not f.reason:
            positional = a.posonlyargs + a.args
            for arg in positional + a.kwonlyargs:
                kind, optional = _hint(arg.annotation)
                has_default = (arg in positional and positional.index(arg) >= len(positional) - len(a.defaults)) or \
                              (arg in a.kwonlyargs and a.kw_defaults[a.kwonlyargs.index(arg)] is not None)
                f.params.append(Param(arg.arg, kind, optional, has_default))
            f.returns_hint = _hint(node.returns)[0]
            f.returns_tuple = any(isinstance(r.value, ast.Tuple) for r in ast.walk(node) if isinstance(r, ast.Return) and r.value is not None) or \
                (isinstance(node.returns, ast.Subscript) and getattr(node.returns.value, "id", "") in ("tuple", "Tuple"))
            problems, helpers, snippets = _closure(m, name, {(m.file, name)})
            f.calls, f.needs = list(dict.fromkeys(helpers)), list(dict.fromkeys(snippets))
            if problems:
                f.reason = problems[0]
        out.append(f)
    return out


def scan(path: Path) -> list[Function]:
    """A .py file, or a folder (every .py file in it, not recursing into tests)."""
    path = Path(path)
    if path.is_file():
        return scan_file(path)
    found = []
    for file in sorted(path.rglob("*.py")):
        if any(part in ("tests", "test", "__pycache__", "docs", "examples") for part in file.parts) or file.name.startswith("test_"):
            continue
        try:
            found += scan_file(file)
        except SyntaxError:
            continue
    return found
