"""Which functions of a C file can be migrated to Rust and checked? Plain code, no model.

A function qualifies when its inputs and its result can be carried as plain values: integers, floats, bool,
a `const char *` string, and a `const <byte> *` buffer followed by its length. Anything that writes through a pointer,
touches files, memory allocation, the clock or a global that changes is refused, with the reason.
"""
from __future__ import annotations

import re
from pathlib import Path

from .scan_python import Function, Param

# C type -> (Rust type, min, max). Widths are the fixed ones; plain int/long are 32 bits on every Windows compiler.
INTS = {"uint8_t": ("u8", 0, 2**8 - 1), "uint16_t": ("u16", 0, 2**16 - 1), "uint32_t": ("u32", 0, 2**32 - 1), "uint64_t": ("u64", 0, 2**64 - 1),
        "int8_t": ("i8", -2**7, 2**7 - 1), "int16_t": ("i16", -2**15, 2**15 - 1), "int32_t": ("i32", -2**31, 2**31 - 1), "int64_t": ("i64", -2**63, 2**63 - 1),
        "unsigned char": ("u8", 0, 255), "signed char": ("i8", -128, 127), "char": ("i8", -128, 127),
        "unsigned short": ("u16", 0, 2**16 - 1), "short": ("i16", -2**15, 2**15 - 1),
        "unsigned int": ("u32", 0, 2**32 - 1), "unsigned": ("u32", 0, 2**32 - 1), "int": ("i32", -2**31, 2**31 - 1),
        "unsigned long": ("u32", 0, 2**32 - 1), "long": ("i32", -2**31, 2**31 - 1),
        "unsigned long long": ("u64", 0, 2**64 - 1), "long long": ("i64", -2**63, 2**63 - 1)}
BYTES = {"uint8_t", "unsigned char", "void", "char"}
LENGTHS = {"size_t", "int", "unsigned", "unsigned int", "uint32_t", "uint64_t", "unsigned long"}
IMPURE = {"malloc": "allocates memory", "calloc": "allocates memory", "realloc": "allocates memory", "free": "frees memory",
          "printf": "prints", "fprintf": "prints", "puts": "prints", "fopen": "opens files", "fread": "reads files", "fwrite": "writes files",
          "rand": "uses randomness", "srand": "uses randomness", "time": "reads the clock", "clock": "reads the clock", "getenv": "reads the environment",
          "exit": "ends the process", "abort": "ends the process", "system": "starts processes"}
KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "do", "else"}


def _clean(text: str) -> str:
    """Comments out, same length and line numbers."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    return re.sub(r"//[^\n]*", blank, re.sub(r"/\*.*?\*/", blank, text, flags=re.S))


def _type(words: str) -> str:
    return " ".join(w for w in words.replace("*", " ").split() if w not in ("const", "static", "inline", "extern", "register", "restrict", "__restrict", "volatile"))


def _top_level(clean: str) -> list[tuple[int, int, int]]:
    """(start of the definition, position of its '{', position after its '}') for every function defined at file level."""
    out, depth, i, last = [], 0, 0, 0
    while i < len(clean):
        ch = clean[i]
        if ch in ";}" and depth == 0:
            last = i + 1
        if ch == "{":
            if depth == 0 and clean[:i].rstrip().endswith(")"):
                j, d = i, 1
                while d and j + 1 < len(clean):
                    j += 1
                    d += {"{": 1, "}": -1}.get(clean[j], 0)
                out.append((last, i, j + 1))
                i = last = j + 1
                continue
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last = i + 1
        i += 1
    return out


def scan(path: Path) -> list[Function]:
    """A .c file, or a folder: every .c file directly in it."""
    path = Path(path)
    if path.is_dir():
        return [f for file in sorted(path.glob("*.c")) for f in scan(file)]
    text = path.read_text(encoding="utf-8", errors="replace")
    clean = _clean(text)
    spans = _top_level(clean)
    heads = {}
    for start, brace, end in spans:
        m = re.search(r"([A-Za-z_]\w*)\s*\(([^()]*)\)\s*$", clean[start:brace], flags=re.S)
        if m and m.group(1) not in KEYWORDS:
            before = re.sub(r"^\s*(#[^\n]*\n\s*)*", "", clean[start:brace][:m.start()])
            heads[m.group(1)] = (start, brace, end, before.strip(), m.group(2).strip())
    # everything at file level that is not a function: includes, macros, typedefs, tables
    rest, at = "", 0
    for start, _, end in spans:
        rest, at = rest + text[at:start], end
    rest = re.sub(r"\n{3,}", "\n\n", rest + text[at:]).strip()
    changing = [m.group(1) for m in re.finditer(r"^(?!\s*(?:#|typedef|extern))(?:static\s+)?(?!const\b)[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:=[^;]*)?;",
                                                _clean(rest), flags=re.M) if " const " not in f" {m.group(0)} "]

    found = []
    for name, (start, brace, end, ret, params_text) in heads.items():
        body = clean[brace:end]
        lead = len(text[start:brace]) - len(text[start:brace].lstrip())
        f = Function(name=name, module=path.stem, file=path, lineno=text.count("\n", 0, start + lead) + 1, source=text[start:end].strip())
        f.calls = [n for n in heads if n != name and re.search(rf"\b{re.escape(n)}\s*\(", body)]
        f.rules, f.c_args, f.rust_returns = {}, [], None
        parts = [] if params_text in ("", "void") else [p.strip() for p in params_text.split(",")]
        i = 0
        while i < len(parts) and not f.reason:
            p = parts[i]
            m = re.match(r"(.*?)([A-Za-z_]\w*)\s*(\[\s*\])?$", p, flags=re.S)
            if not m or "(" in p or "..." in p:
                f.reason = f"takes `{p}`, which I cannot carry as a plain value"
                break
            words, pname, stars = m.group(1), m.group(2), m.group(1).count("*") + bool(m.group(3))
            base = _type(words)
            if stars == 0 and base in INTS:
                rust, lo, hi = INTS[base]
                f.rules[pname] = {"type": "int", "min": lo, "max": hi, "rust": rust}
                f.c_args.append(("int", pname, base))
            elif stars == 0 and base in ("float", "double"):
                f.rules[pname] = {"type": "float", "min": -1e6, "max": 1e6, "rust": "f64" if base == "double" else "f32"}
                f.c_args.append(("float", pname, base))
            elif stars == 0 and base in ("bool", "_Bool"):
                f.rules[pname] = {"type": "bool", "rust": "bool"}
                f.c_args.append(("int", pname, "int"))
            elif stars == 0 and base == "size_t":
                f.rules[pname] = {"type": "int", "min": 0, "max": 2**32 - 1, "rust": "usize"}
                f.c_args.append(("int", pname, "size_t"))
            elif stars == 1 and "const" in words.split() and base in BYTES:
                nxt = re.match(r"(.*?)([A-Za-z_]\w*)$", parts[i + 1], flags=re.S) if i + 1 < len(parts) else None
                if nxt and "*" not in nxt.group(1) and _type(nxt.group(1)) in LENGTHS:
                    f.rules[pname] = {"type": "list[int]", "min": 0, "max": 255, "max_items": 64, "rust": "Vec<u8>"}
                    f.c_args.append(("bytes", pname, _type(nxt.group(1))))
                    i += 1                                   # the length travels with the buffer, it is not an input of its own
                elif base == "char":
                    f.rules[pname] = {"type": "str", "max_len": 40, "rust": "String"}
                    f.c_args.append(("str", pname, "char"))
                else:
                    f.reason = f"takes the buffer `{pname}` with no length after it"
            elif stars == 1 and "const" in words.split() and base in INTS and i + 1 < len(parts) and "*" not in parts[i + 1]                     and _type(re.sub(r"[A-Za-z_]\w*$", "", parts[i + 1])) in LENGTHS:
                rust, lo, hi = INTS[base]                    # an array of whole numbers and its length
                f.rules[pname] = {"type": "list[int]", "min": lo, "max": hi, "max_items": 16, "rust": f"Vec<{rust}>"}
                f.c_args.append(("array", pname, base + "|" + _type(re.sub(r"[A-Za-z_]\w*$", "", parts[i + 1]))))
                i += 1
            elif stars:
                f.reason = f"writes through or keeps the pointer `{pname}`" if "const" not in words.split() else f"takes a pointer to `{base}`, which I cannot carry as a plain value"
            else:
                f.reason = f"takes a `{base}`, which I cannot carry as a plain value"
            i += 1
        base = _type(ret)
        if not f.reason and (name == "main" or not f.rules):
            f.reason = "takes no inputs"
        if not f.reason:
            if "*" in ret:
                f.reason = "returns a pointer"
            elif base in INTS:
                f.rust_returns = INTS[base][0]
            elif base == "size_t":
                f.rust_returns = "usize"
            elif base in ("float", "double"):
                f.rust_returns = "f64" if base == "double" else "f32"
            elif base in ("bool", "_Bool"):
                f.rust_returns = "bool"
            elif base == "void":
                f.reason = "returns nothing, so there is nothing to compare"
            else:
                f.reason = f"returns a `{base}`, which I cannot carry as a plain value"
        if not f.reason:
            used = next((w for w in IMPURE if re.search(rf"\b{w}\s*\(", body)), None)
            if used:
                f.reason = f"{IMPURE[used]} (`{used}`)"
            elif re.search(r"\bstatic\s+(?!const\b)", body):
                f.reason = "keeps a `static` variable between calls, so calls affect each other"
            else:
                g = next((g for g in changing if re.search(rf"\b{re.escape(g)}\b", body)), None)
                if g:
                    f.reason = f"uses the file-level variable `{g}`, which can change, so calls affect each other"
        found.append(f)

    by_name = {f.name: f for f in found}
    for f in found:                                           # a function is only as clean as what it calls
        todo, seen = list(f.calls), set()
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            todo += by_name[n].calls
        f.calls = [n for n in heads if n in seen]
        bad = next((by_name[n] for n in f.calls if by_name[n].reason and "plain value" not in by_name[n].reason and "pointer" not in by_name[n].reason
                    and "buffer" not in by_name[n].reason and "returns nothing" not in by_name[n].reason), None)
        if bad and not f.reason:
            f.reason = f"calls {bad.name}(), which {bad.reason}"
        f.params = [Param(name=n, type=f.rules[n]["type"]) for n in f.rules]
        f.needs = [rest] + [by_name[n].source for n in f.calls]
    return found
