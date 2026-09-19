"""Serial integrator: the only writer of the accepted tree.

A candidate that passed its own gate check is re-checked here on the exact tree it will
join: all previously accepted files + this candidate, against the cases of every accepted
chunk + this one, under the CURRENT contract versions. Only then is it written to
runs/<id>/accepted/ and given a receipt. One at a time, under a lock.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from . import gate
from .contracts import ContractLedger
from .events import EventLog


class Integrator:
    def __init__(self, profile_dir: Path, run_dir: Path, cases_path: Path, ledger: ContractLedger, events: EventLog) -> None:
        self.profile_dir, self.run_dir, self.cases_path = Path(profile_dir), Path(run_dir), Path(cases_path)
        self.ledger, self.events = ledger, events
        self.files: dict[str, str] = {}          # accepted tree overlay: path -> content
        self.owner: dict[str, str] = {}          # path -> chunk that owns it
        self.accepted: list[str] = []            # chunk ids with a valid receipt, in integration order
        self.stale: set[str] = set()
        self._lock = threading.Lock()
        self._n = 0

    def load_existing(self, chunks: dict, fixture_hash: str) -> list[str]:
        """Resume: reuse chunks accepted earlier in this run, but only if their evidence still holds.

        A receipt is reused when it is ACCEPTED, was issued for the same frozen fixture, and was issued
        under the contract versions that are current now. Anything else is redone.
        """
        reused = []
        for path in sorted((self.run_dir / "receipts").glob("*.json"), key=lambda p: p.stat().st_mtime):
            r = json.loads(path.read_text(encoding="utf-8"))
            cid = r["chunk_id"]
            want = self.ledger.hashes(chunks.get(cid, {}).get("contract_ids", []))
            have = {k: v for k, v in r.get("contract_hashes", {}).items() if k in want}
            files = {p: self.run_dir / "accepted" / p for p in chunks.get(cid, {}).get("write_allowlist", [])}
            if (r.get("status") != "ACCEPTED" or r.get("fixture_hash") != fixture_hash or have != want
                    or not files or not all(f.exists() for f in files.values())
                    or not set(chunks[cid].get("depends_on", [])) <= set(self.accepted)):
                continue
            for p, f in files.items():
                self.files[p], self.owner[p] = f.read_text(encoding="utf-8"), cid
            self.accepted.append(cid)
            reused.append(cid)
            self.events.emit("chunk.resumed", actor="integrator", chunk_id=cid, payload={"reason": "ACCEPTED", "receipt": path.name})
        return reused

    def tree_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.files, sort_keys=True).encode()).hexdigest()

    def files_of(self, chunk_ids: list[str]) -> dict[str, str]:
        return {p: c for p, c in self.files.items() if self.owner[p] in chunk_ids}

    def integrate(self, chunk_id: str, candidate: dict) -> gate.Verdict:
        with self._lock:
            self._n += 1
            others = [c for c in self.accepted if c != chunk_id]
            overlay = {p: c for p, c in self.files.items() if self.owner[p] != chunk_id}
            v = gate.check(self.profile_dir, chunk_id, candidate, self.cases_path, self.run_dir,
                           attempt_id=f"{chunk_id}:integrate-{self._n}",
                           current_contract_hashes=self.ledger.hashes(), events=self.events,
                           overlay=overlay, case_chunks=others + [chunk_id],
                           fail_status="REJECTED_INTEGRATION", accept_event="chunk.accepted")
            if not v.accepted:
                return v
            for p in [p for p, o in self.owner.items() if o == chunk_id]:
                del self.files[p], self.owner[p]
            for f in candidate["files"]:
                self.files[f["path"]], self.owner[f["path"]] = f["content"], chunk_id
                dest = self.run_dir / "accepted" / f["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(f["content"], encoding="utf-8", newline="\n")
            if chunk_id not in self.accepted:
                self.accepted.append(chunk_id)
            self.stale.discard(chunk_id)
            receipt = {
                "schema_version": 1, "run_id": self.run_dir.name, "chunk_id": chunk_id, "attempt_id": v.attempt_id,
                "status": "ACCEPTED", "accepted_tree": self.tree_hash(), "accepted_chunks": list(self.accepted),
                "contract_hashes": self.ledger.hashes(), "fixture_hash": v.fixture_hash,
                "candidate_hash": v.candidate_hash,
                "verifier_hash": hashlib.sha256(Path(gate.__file__).read_bytes()).hexdigest(),
                "cases": v.cases, "checks": {"build": "PASS", "inventory": "PASS", "differential": "PASS", "integration": "PASS"},
                "locked_evaluation": "NOT_RUN",
                "limitations": ["Finite testing within the declared input domain"],
            }
            out = self.run_dir / "receipts"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{chunk_id}.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
            return v

    def invalidate(self, chunk_ids: list[str], reason: str) -> list[str]:
        """A contract changed: receipts of accepted chunks that relied on it are no longer valid."""
        with self._lock:
            hit = [c for c in chunk_ids if c in self.accepted and c not in self.stale]
            for c in hit:
                self.stale.add(c)
                path = self.run_dir / "receipts" / f"{c}.json"
                receipt = json.loads(path.read_text(encoding="utf-8"))
                receipt.update(status="STALE", stale_reason=reason)
                path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
                self.events.emit("candidate.stale", actor="integrator", chunk_id=c, payload={"reason": "STALE", "detail": reason})
            return hit

    @property
    def exportable(self) -> bool:
        return not self.stale
