"""Append-only event log. The engine is the only writer; D's report and the CLI only read it."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


class EventLog:
    def __init__(self, run_dir: Path, run_id: str) -> None:
        self.run_id = run_id
        self.path = Path(run_dir) / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Resume-safe: continue the sequence from whatever is already on disk.
        self._seq = sum(1 for _ in self.path.open(encoding="utf-8")) if self.path.exists() else 0

    def emit(self, type: str, *, actor: str, profile: str | None = None, chunk_id: str | None = None,
             attempt_id: str | None = None, payload: dict | None = None) -> dict:
        with self._lock:
            self._seq += 1
            event = {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "sequence": self._seq,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "type": type,
                "profile": profile,
                "chunk_id": chunk_id,
                "attempt_id": attempt_id,
                "actor": actor,
                "payload": payload or {},
            }
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            return event
