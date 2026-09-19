"""Contract ledger: versioned implementation guidance, and who is affected when it changes.

A contract's `behavior` is frozen before the run and nothing here can change it.
The steward (an LLM) may only PROPOSE `implementation_clarification` decisions; this
service validates them, bumps the version, and reports the affected chunks. A candidate
written against an older version is stale (the gate's provenance stage rejects it).
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from .events import EventLog

ALLOWED_KINDS = {"implementation_clarification"}


class DecisionRejected(Exception):
    pass


class ContractLedger:
    def __init__(self, profile_dir: Path, run_dir: Path, chunks: dict[str, dict], events: EventLog | None = None) -> None:
        self.profile_dir, self.run_dir = Path(profile_dir), Path(run_dir)
        self.chunks, self.events = chunks, events
        self._lock = threading.Lock()
        self._dir = self.run_dir / "contracts"
        self._dir.mkdir(parents=True, exist_ok=True)
        self.contracts: dict[str, dict] = {}
        for src in sorted((self.profile_dir / "contracts").glob("*.json")):
            live = self._dir / src.name
            # Resume keeps the run's versions; a fresh run starts from the frozen fixture.
            data = json.loads((live if live.exists() else src).read_text(encoding="utf-8"))
            data.setdefault("version", 1)
            data.setdefault("guidance", [])
            self.contracts[data["contract_id"]] = data
            self._save(data)

    def _save(self, c: dict) -> None:
        (self._dir / f"{c['contract_id']}.json").write_text(json.dumps(c, indent=2) + "\n", encoding="utf-8", newline="\n")

    @staticmethod
    def _hash(c: dict) -> str:
        blob = json.dumps([c["contract_id"], c["version"], c["behavior"], c["guidance"]], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def hashes(self, contract_ids: list[str] | None = None) -> dict[str, str]:
        with self._lock:
            return {cid: self._hash(c) for cid, c in self.contracts.items() if contract_ids is None or cid in contract_ids}

    def prompt_text(self, contract_ids: list[str]) -> str:
        with self._lock:
            out = []
            for cid in contract_ids:
                c = self.contracts[cid]
                out.append(f"[contract {cid} v{c['version']}] Frozen behavior: {c['behavior']}")
                out += [f"  - Implementation guidance (decision {g['decision_id']}): {g['ruling']}" for g in c["guidance"]]
            return "\n".join(out)

    def affected_chunks(self, contract_id: str) -> list[str]:
        """Direct users of the contract plus everything that depends on them. If unsure, over-include."""
        hit = {cid for cid, ch in self.chunks.items() if contract_id in ch.get("contract_ids", [])}
        grew = True
        while grew:
            more = {cid for cid, ch in self.chunks.items() if set(ch.get("depends_on", [])) & hit} - hit
            hit |= more
            grew = bool(more)
        return sorted(hit)

    def record_decision(self, proposal: dict, *, proposed_by: str, allowed_contracts: list[str]) -> dict:
        kind, cid = proposal.get("kind"), proposal.get("contract_id")
        ruling = (proposal.get("ruling") or "").strip()
        if kind not in ALLOWED_KINDS:
            raise DecisionRejected(f"kind {kind!r} is not allowed; behavior changes need a human")
        if cid not in self.contracts or cid not in allowed_contracts:
            raise DecisionRejected(f"contract {cid!r} is not one this chunk uses")
        if not ruling:
            raise DecisionRejected("empty ruling")
        refs = [r for r in proposal.get("evidence_refs") or [] if isinstance(r, str)]
        if not refs:
            raise DecisionRejected("no evidence_refs")
        with self._lock:
            c = self.contracts[cid]
            n = sum(1 for _ in (self.run_dir / "decisions.jsonl").open(encoding="utf-8")) if (self.run_dir / "decisions.jsonl").exists() else 0
            decision = {
                "schema_version": 1, "decision_id": f"D-{n + 1:03d}", "contract_id": cid,
                "version": c["version"] + 1, "supersedes": c["version"], "kind": kind,
                "question": proposal.get("question", ""), "ruling": ruling, "evidence_refs": refs,
                "affected_chunks": self.affected_chunks(cid), "proposed_by": proposed_by,
                "accepted_by": "contract-service",
            }
            c["version"] += 1
            c["guidance"].append({"decision_id": decision["decision_id"], "ruling": ruling})
            decision["content_hash"] = self._hash(c)
            self._save(c)
            with (self.run_dir / "decisions.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(decision, ensure_ascii=False) + "\n")
        if self.events:
            self.events.emit("decision.recorded", actor="contract-service", payload=decision)
        return decision
