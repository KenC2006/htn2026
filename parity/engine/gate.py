"""The gate: deterministic, no LLM. Decides whether one worker candidate is accepted.

Stage order (first failure wins):
  provenance -> patch boundary -> target constraints -> harness integrity -> build
  -> case inventory -> differential behavior -> ACCEPTED

Trust boundary:
- The worker only hands over file contents. It never runs anything.
- The candidate is built and run in a fresh workspace that does NOT contain the
  original source (profile `oracle_paths`), the expected outputs, or credentials.
- Source observations are produced from the frozen fixture and kept outside the workspace.

Formats: CONTRACTS.md. Usage:
  python -m parity.engine.gate freeze <profile_dir>
  python -m parity.engine.gate check <profile_dir> --chunk P2 --candidate cand.json --cases cases.jsonl --run-dir runs/x
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from .events import EventLog

LOCK_NAME = "frozen.lock.json"
MAX_FILE_BYTES = 200_000
_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|API_BASE|CREDENTIAL)", re.I)
CASES_NAME = "_cases.jsonl"

_BUILD_LOCK = threading.Lock()
_ORACLE: dict = {}
_ORACLE_LOCK = threading.Lock()
_PARSED: dict = {}
_PARSED_LOCK = threading.Lock()


@dataclass
class Verdict:
    status: str                      # ACCEPTED | STALE | REJECTED_* | BLOCKED
    stage: str
    detail: str = ""
    chunk_id: str = ""
    attempt_id: str = ""
    profile: str = ""
    cases: dict = field(default_factory=dict)
    counterexample: dict | None = None
    counterexamples: list = field(default_factory=list)
    mismatched_case_ids: list = field(default_factory=list)
    contract_hashes: dict = field(default_factory=dict)
    fixture_hash: str = ""
    candidate_hash: str = ""
    logs: dict = field(default_factory=dict)
    duration_s: float = 0.0

    @property
    def accepted(self) -> bool:
        return self.status == "ACCEPTED"


# ───────────────────────── helpers ─────────────────────────

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parsed(path: Path, load):
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size, load.__name__)
    with _PARSED_LOCK:
        hit = _PARSED.get(key)
    if hit is not None:
        return hit
    val = load(path)
    with _PARSED_LOCK:
        _PARSED[key] = val
        while len(_PARSED) > 32:
            _PARSED.pop(next(iter(_PARSED)))
    return val


def _scrubbed_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # runners must not write into the frozen fixture
    return env


def _is_junk(rel: str) -> bool:
    return "__pycache__" in rel.split("/") or rel.endswith(".pyc")


def _cmd(argv: list[str]) -> list[str]:
    return [sys.executable, *argv[1:]] if argv and argv[0] == "python" else list(argv)


def _run(argv: list[str], cwd: Path, timeout: float) -> tuple[int | None, str]:
    """Returns (exit code or None on timeout, combined output tail)."""
    try:
        p = subprocess.run(_cmd(argv), cwd=cwd, env=_scrubbed_env(), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-4000:]
    except subprocess.TimeoutExpired as e:
        return None, f"timeout after {timeout}s\n{(e.stdout or '')[-2000:] if isinstance(e.stdout, str) else ''}"
    except FileNotFoundError as e:
        return 127, str(e)


def _frozen_files(profile_dir: Path, profile: dict) -> list[str]:
    patterns = profile.get("frozen", [])
    out, stack = [], [(str(profile_dir), "")]
    while stack:
        d, base = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                rel = base + e.name
                if e.is_dir(follow_symlinks=False):
                    if e.name != "__pycache__":
                        stack.append((e.path, rel + "/"))
                elif rel != LOCK_NAME and not _is_junk(rel) and any(fnmatch.fnmatch(rel, pat) for pat in patterns):
                    out.append(rel)
    return out


def freeze(profile_dir: Path) -> dict:
    """Record hashes of everything workers must never change. Run once before any worker runs."""
    profile_dir = Path(profile_dir)
    profile = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))
    files = {rel: _sha((profile_dir / rel).read_bytes()) for rel in sorted(_frozen_files(profile_dir, profile))}
    lock = {"schema_version": 1, "files": files,
            "fixture_hash": _sha(json.dumps(files, sort_keys=True).encode())}
    (profile_dir / LOCK_NAME).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n")
    return lock


def _bad_path(rel: str) -> str | None:
    if not rel or "\\" in rel or "\0" in rel:
        return "empty path, backslash or NUL"
    p = PurePosixPath(rel)
    if p.is_absolute() or ":" in rel or any(part in ("..", ".") for part in p.parts):
        return "absolute or traversing path"
    return None


# ───────────────────────── the gate ─────────────────────────

def check(profile_dir: Path, chunk_id: str, candidate: dict, cases_path: Path, run_dir: Path, *,
          attempt_id: str | None = None, current_contract_hashes: dict | None = None,
          events: EventLog | None = None, overlay: dict | None = None,
          case_chunks: list | None = None, fail_status: str | None = None,
          accept_event: str = "candidate.verified", stop_after_build: bool = False) -> Verdict:
    """overlay: already-accepted files {path: content} laid down before the candidate's.
    case_chunks: run these chunks' cases instead of only `chunk_id`'s (integration re-check).
    fail_status: report build/test/behavior failures under this status (REJECTED_INTEGRATION)."""
    t0 = time.monotonic()
    profile_dir, run_dir = Path(profile_dir).resolve(), Path(run_dir).resolve()
    profile = _parsed(profile_dir / "profile.json", _read_json)
    chunk = _parsed(profile_dir / "chunks" / f"{chunk_id}.json", _read_json)
    attempt_id = attempt_id or f"{chunk_id}:1"
    files = candidate.get("files") or []
    v = Verdict(status="", stage="", chunk_id=chunk_id, attempt_id=attempt_id, profile=profile["profile"],
                contract_hashes=dict(current_contract_hashes or {}),
                candidate_hash=_sha(json.dumps(files, sort_keys=True).encode()))

    sources: dict = {}   # workspace path -> the files it held before anything was built

    def done(status: str, stage: str, detail: str = "") -> Verdict:
        for root, before in sources.items():          # drop build outputs (executables, debug files): ~1 MB per check otherwise
            for f in sorted(root.rglob("*"), reverse=True):
                if f.is_file() and f not in before:
                    f.unlink(missing_ok=True)
                elif f.is_dir() and not any(f.iterdir()):
                    f.rmdir()
        if fail_status and status in ("REJECTED_BUILD", "REJECTED_TEST", "REJECTED_BEHAVIOR"):
            detail, status = f"{status}: {detail}", fail_status
        v.status, v.stage, v.detail = status, stage, detail
        v.duration_s = round(time.monotonic() - t0, 2)
        out = run_dir / "verdicts"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{attempt_id.replace(':', '-')}.json").write_text(
            json.dumps(asdict(v), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        if events:
            events.emit(accept_event if v.accepted else ("candidate.stale" if status == "STALE" else "candidate.rejected"),
                        actor="verifier", profile=v.profile, chunk_id=chunk_id, attempt_id=attempt_id,
                        payload={"reason": status, "stage": stage, "detail": detail[:500],
                                 "case_id": (v.counterexample or {}).get("case_id"), "cases": v.cases})
        return v

    if events:
        events.emit("candidate.submitted", actor="verifier", profile=v.profile, chunk_id=chunk_id,
                    attempt_id=attempt_id, payload={"files": [f.get("path") for f in files]})

    # 1. Provenance: the candidate must have been written against the current contract versions.
    used = candidate.get("contract_hashes") or {}
    for cid in chunk.get("contract_ids", []):
        cur = (current_contract_hashes or {}).get(cid)
        if cur is not None and used.get(cid) != cur:
            return done("STALE", "provenance", f"contract {cid}: candidate used {used.get(cid)!r}, current is {cur!r}")

    # 2. Patch boundary.
    if not files:
        return done("REJECTED_POLICY", "patch_boundary", "candidate contains no files")
    allow = set(chunk["write_allowlist"])
    for f in files:
        rel, content = f.get("path", ""), f.get("content")
        why = _bad_path(rel)
        if why:
            return done("REJECTED_POLICY", "patch_boundary", f"{rel!r}: {why}")
        if rel not in allow:
            return done("REJECTED_POLICY", "patch_boundary", f"{rel!r} is not in this chunk's write_allowlist")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            return done("REJECTED_POLICY", "patch_boundary", f"{rel!r}: content must be text under {MAX_FILE_BYTES} bytes")

    # 3. Target constraints (cheap textual bans; the compiler-enforced ones live in the frozen harness).
    for f in files:
        for rule in profile.get("forbid_patterns", []):
            m = re.search(rule["regex"], f["content"])
            if m:
                return done("REJECTED_POLICY", "target_constraints", f"{f['path']}: {rule['why']} ({m.group(0)!r})")

    # 4. Harness integrity: the fixture must still match what was frozen before workers ran.
    lock_path = profile_dir / LOCK_NAME
    if not lock_path.exists():
        return done("BLOCKED", "harness_integrity", f"no {LOCK_NAME}; run `gate freeze` before any worker runs")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    v.fixture_hash = lock["fixture_hash"]
    if set(_frozen_files(profile_dir, profile)) != set(lock["files"]):
        return done("REJECTED_INTEGRITY", "harness_integrity", "frozen file set changed since freeze")
    cand_paths = {f["path"] for f in files}
    for rel, digest in lock["files"].items():
        if _sha((profile_dir / rel).read_bytes()) != digest:
            return done("REJECTED_INTEGRITY", "harness_integrity", f"{rel} changed since freeze")
        if rel in cand_paths:
            return done("REJECTED_INTEGRITY", "harness_integrity", f"candidate tries to replace frozen file {rel}")

    # Fresh workspace: fixture minus the oracle, plus the candidate's files.
    ws = run_dir / "candidates" / attempt_id.replace(":", "-")
    if ws.exists():
        shutil.rmtree(ws)
    hidden = list(profile.get("oracle_paths", []))

    def _ignore(d: str, names: list[str]) -> list[str]:
        base = Path(d).resolve().relative_to(profile_dir).as_posix()
        skip = []
        for n in names:
            rel = n if base == "." else f"{base}/{n}"
            if _is_junk(rel) or any(rel == h or rel.startswith(h.rstrip("/") + "/") or fnmatch.fnmatch(rel, h) for h in hidden):
                skip.append(n)
        return skip

    shutil.copytree(profile_dir, ws, ignore=_ignore)
    for f in [{"path": k, "content": c} for k, c in (overlay or {}).items()] + files:
        dest = ws / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f["content"], encoding="utf-8", newline="\n")
    sources[ws] = {f for f in ws.rglob("*") if f.is_file()}

    # Only this chunk's cases. Inputs go into the workspace; expected outputs never do.
    wanted = set(case_chunks or [chunk_id])
    cases = [c for c in _parsed(Path(cases_path), _read_jsonl) if c.get("chunk_id") is None or c["chunk_id"] in wanted]
    ids = [c["case_id"] for c in cases]
    if not cases or len(set(ids)) != len(ids):
        return done("BLOCKED", "case_inventory", "no cases for this chunk, or duplicate case_id in the case file")
    obs_dir = run_dir / "observations" / attempt_id.replace(":", "-")
    obs_dir.mkdir(parents=True, exist_ok=True)
    ws_cases = ws / CASES_NAME
    ws_cases.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8", newline="\n")
    limit = float(chunk.get("limits", {}).get("verify_seconds_per_attempt") or profile.get("verify_seconds", 180))

    # 5. Build with the real target compiler.
    if profile.get("build_target"):
        argv = profile.get("compile_target", profile["build_target"]) if stop_after_build else profile["build_target"]
        pre = {f for f in ws.rglob("*") if f.is_file()}
        which = shutil.which(argv[0]) or argv[0]
        key = _sha(json.dumps([argv, which, os.path.getmtime(which) if os.path.exists(which) else 0,
                               sorted((p.relative_to(ws).as_posix(), _sha(p.read_bytes()))
                                      for p in pre if p.name != CASES_NAME)]).encode())[:32]   # short: MAX_PATH on Windows
        cached = run_dir / ".build-cache" / key
        if (cached / "fail.log").exists():
            v.logs["build"] = (cached / "fail.log").read_text(encoding="utf-8")
            return done("REJECTED_BUILD", "build", v.logs["build"][-1500:])
        if (cached / "out").is_dir():
            for p in (cached / "out").rglob("*"):
                if p.is_file():
                    dest = ws / p.relative_to(cached / "out")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)     # copy2, not copyfile: the restored binary must stay executable
            v.logs["build"] = (cached / "log.txt").read_text(encoding="utf-8") if (cached / "log.txt").exists() else ""
        else:
            code, log = _run(argv, ws, limit)
            v.logs["build"] = log
            if code is not None:
                tmp = run_dir / ".build-cache" / f"t{os.getpid()}-{threading.get_ident()}"
                shutil.rmtree(tmp, ignore_errors=True)   # a leftover from a killed run must not be published
                (tmp / "out").mkdir(parents=True, exist_ok=True)
                if code == 0:
                    for p in {f for f in ws.rglob("*") if f.is_file()} - pre:
                        dest = tmp / "out" / p.relative_to(ws)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, dest)
                    (tmp / "log.txt").write_text(log, encoding="utf-8", newline="\n")
                else:
                    (tmp / "fail.log").write_text(log, encoding="utf-8", newline="\n")
                with _BUILD_LOCK:
                    try:
                        os.replace(tmp, cached)
                    except OSError:
                        shutil.rmtree(tmp, ignore_errors=True)
            if code != 0:
                return done("REJECTED_BUILD", "build", log[-1500:])

    if stop_after_build:  # compile-only tool for workers: no cases run, nothing about behavior is revealed
        return done("BUILD_OK", "build", v.logs.get("build", "")[-1500:])

    # Source oracle runs from the frozen fixture, never from the workspace.
    src_obs_path = obs_dir / "source_obs.jsonl"
    fkey = _sha(json.dumps(lock["files"], sort_keys=True).encode())
    okeys = {c["case_id"]: (fkey, c.get("export"), _sha(json.dumps(c["input"], sort_keys=True).encode())) for c in cases}
    with _ORACLE_LOCK:
        src = {cid: dict(_ORACLE[k], case_id=cid) for cid, k in okeys.items() if k in _ORACLE}
    miss = [c for c in cases if c["case_id"] not in src]
    if miss:
        miss_cases, miss_obs = ws_cases, src_obs_path
        if len(miss) != len(cases):
            miss_cases, miss_obs = obs_dir / "miss_cases.jsonl", obs_dir / "miss_obs.jsonl"
            miss_cases.write_text("".join(json.dumps(c) + "\n" for c in miss), encoding="utf-8", newline="\n")
        code, log = _run([*profile["run_source"], "--cases", str(miss_cases), "--out", str(miss_obs)], profile_dir, limit)
        v.logs["run_source"] = log
        if code != 0 or not miss_obs.exists():
            return done("BLOCKED", "oracle", f"source runner failed (exit {code}): {log[-800:]}")
        fresh = {o["case_id"]: o for o in _read_jsonl(miss_obs)}
        src.update(fresh)
        with _ORACLE_LOCK:
            for c in miss:
                o = fresh.get(c["case_id"])
                if o and o.get("status") in ("ok", "error"):
                    _ORACLE[okeys[c["case_id"]]] = o
    src_obs_path.write_text("".join(json.dumps(src[c["case_id"]]) + "\n" for c in cases if c["case_id"] in src),
                            encoding="utf-8", newline="\n")
    bad_oracle = [i for i in ids if i not in src or src[i]["status"] not in ("ok", "error")]
    if bad_oracle:
        return done("BLOCKED", "oracle", f"oracle has no defined behavior for {bad_oracle[:5]}")

    tgt_obs_path = ws / "_target_obs.jsonl"
    code, log = _run([*profile["run_target"], "--cases", str(ws_cases), "--out", str(tgt_obs_path), "--candidate", str(ws)], ws, limit)
    v.logs["run_target"] = log
    if code is None:
        return done("REJECTED_TEST", "case_inventory", f"target run timed out after {limit}s")
    if code != 0 or not tgt_obs_path.exists():
        return done("REJECTED_TEST", "case_inventory", f"target runner failed (exit {code}): {log[-800:]}")
    tgt_list = _read_jsonl(tgt_obs_path)
    shutil.copy(tgt_obs_path, obs_dir / "target_obs.jsonl")

    # 6. Case inventory: every expected case ran exactly once. Zero tests is never a pass.
    seen = [o.get("case_id") for o in tgt_list]
    missing, extra = sorted(set(ids) - set(seen)), sorted(set(seen) - set(ids))
    dupes = sorted({i for i in seen if seen.count(i) > 1})
    v.cases = {"expected": len(ids), "observed": len(seen), "passed": 0, "skipped": len(missing)}
    if missing or extra or dupes:
        return done("REJECTED_TEST", "case_inventory", f"missing={missing[:5]} duplicate={dupes[:5]} unexpected={extra[:5]}")
    tgt = {o["case_id"]: o for o in tgt_list}

    # 7. Differential: exact comparison of declared observations. Crash and timeout are failures.
    by_id = {c["case_id"]: c for c in cases}
    for cid in ids:
        s, t = src[cid], tgt[cid]
        same = (t.get("status") in ("ok", "error")
                and (s["status"], s.get("value"), s.get("error_code")) == (t["status"], t.get("value"), t.get("error_code")))
        if same:
            v.cases["passed"] += 1
        else:
            v.mismatched_case_ids.append(cid)
    if v.mismatched_case_ids:
        n = len(v.mismatched_case_ids)
        pick = v.mismatched_case_ids[:: max(1, n // 5)][:5]
        v.counterexamples = [{"case_id": c, "input": by_id[c]["input"],
                              "source": {k: src[c].get(k) for k in ("status", "value", "error_code")},
                              "target": {k: tgt[c].get(k) for k in ("status", "value", "error_code", "diagnostics")}}
                             for c in pick]
        v.counterexample = v.counterexamples[0]
        return done("REJECTED_BEHAVIOR", "differential", f"{n} of {len(ids)} cases differ; first: {v.mismatched_case_ids[0]}")

    return done("ACCEPTED", "differential", f"{len(ids)} of {len(ids)} cases match")


def main() -> None:
    ap = argparse.ArgumentParser(prog="parity.engine.gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fz = sub.add_parser("freeze")
    fz.add_argument("profile_dir")
    ck = sub.add_parser("check")
    ck.add_argument("profile_dir")
    ck.add_argument("--chunk", required=True)
    ck.add_argument("--candidate", required=True)
    ck.add_argument("--cases", required=True)
    ck.add_argument("--run-dir", required=True)
    ns = ap.parse_args()
    if ns.cmd == "freeze":
        lock = freeze(Path(ns.profile_dir))
        print(f"froze {len(lock['files'])} files, fixture_hash={lock['fixture_hash'][:12]}")
        return
    run_dir = Path(ns.run_dir)
    verdict = check(Path(ns.profile_dir), ns.chunk, json.loads(Path(ns.candidate).read_text(encoding="utf-8")),
                    Path(ns.cases), run_dir, events=EventLog(run_dir, run_dir.name))
    print(json.dumps({k: val for k, val in asdict(verdict).items() if k != "logs"}, indent=2, ensure_ascii=False))
    sys.exit(0 if verdict.accepted else 1)


if __name__ == "__main__":
    main()
