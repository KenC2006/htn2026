"""Locked evaluation: run the ACCEPTED tree once against cases no agent and no repair loop ever saw.

Development cases drive repair, so passing them is partly the result of feedback. Locked cases are
generated with separate seeds, are frozen and hidden like the oracle, and are used exactly once, after
the run is over. A failure is kept in the receipts and makes the run non-exportable; it is never fed
back to a worker (a repair would be a new run and would need a new locked set).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import gate
from .events import EventLog


def locked_evaluate(run_dir: Path, profile_dir: Path, *, force: bool = False) -> dict:
    run_dir, profile_dir = Path(run_dir), Path(profile_dir)
    profile = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))
    rel = profile.get("locked_cases")
    if not rel or not (profile_dir / rel).exists():
        return {"status": "NOT_AVAILABLE", "detail": "profile declares no locked_cases file"}
    locked = profile_dir / rel
    chunks = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((profile_dir / "chunks").glob("*.json"))}
    receipts = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((run_dir / "receipts").glob("*.json"))}
    accepted = [c for c, r in receipts.items() if r["status"] == "ACCEPTED"]
    if not accepted:
        return {"status": "NOT_RUN", "detail": "no accepted chunks to evaluate"}
    if not force and any(isinstance(r.get("locked_evaluation"), dict) for r in receipts.values()):
        return {"status": "ALREADY_RUN", "detail": "locked cases are single-use for a run; results are in the receipts"}

    files = {p.relative_to(run_dir / "accepted").as_posix(): p.read_text(encoding="utf-8")
             for p in sorted((run_dir / "accepted").rglob("*")) if p.is_file()}
    owner = {path: cid for cid in accepted for path in chunks[cid]["write_allowlist"]}
    case_hash = hashlib.sha256(locked.read_bytes()).hexdigest()
    events = EventLog(run_dir, run_dir.name)
    summary = {"status": "PASS", "case_manifest_hash": case_hash, "chunks": {}}

    for cid in accepted:
        mine = [{"path": p, "content": c} for p, c in files.items() if owner.get(p) == cid]
        others = {p: c for p, c in files.items() if owner.get(p) != cid}
        v = gate.check(profile_dir, cid, {"files": mine}, locked, run_dir, attempt_id=f"{cid}:locked-eval",
                       overlay=others, case_chunks=[cid])
        ok = v.accepted
        result = {"status": "PASS" if ok else "FAIL", "cases": v.cases, "case_manifest_hash": case_hash,
                  "failures": v.mismatched_case_ids[:20], "first_failure": v.counterexample, "detail": "" if ok else v.detail[:300]}
        summary["chunks"][cid] = result
        receipts[cid]["locked_evaluation"] = result
        if not ok:
            summary["status"] = "FAIL"
            receipts[cid]["status"] = "FAILED_LOCKED_EVALUATION"
        (run_dir / "receipts" / f"{cid}.json").write_text(json.dumps(receipts[cid], indent=2) + "\n", encoding="utf-8", newline="\n")
        events.emit("evaluation.locked", actor="verifier", chunk_id=cid,
                    payload={"reason": result["status"], "cases": v.cases, "case_id": (v.counterexample or {}).get("case_id")})
    return summary
