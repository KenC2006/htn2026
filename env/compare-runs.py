"""Summarise runs side by side from their event logs: python env/compare-runs.py rel-1 rel-2 solo-1 ..."""
import json, sys
from datetime import datetime
from pathlib import Path
root = Path(__file__).resolve().parents[1] / "runs"
print(f"{'run':<10}{'mode':<8}{'accepted':<10}{'gate_rejects':<14}{'stale':<7}{'questions':<11}{'probes':<8}{'decisions':<11}{'wall_s':<8}{'tokens':<9}{'cost_usd'}")
for rid in sys.argv[1:]:
    ev = [json.loads(l) for l in (root / rid / "events.jsonl").open(encoding="utf-8")]
    n = lambda t: sum(1 for e in ev if e["type"] == t)
    ts = [datetime.strptime(e["timestamp"], "%Y-%m-%dT%H:%M:%SZ") for e in ev]
    fin = next((e["payload"] for e in ev if e["type"] == "run.finished"), None)
    tokens = cost = "?"
    out = root / f"{rid}.out"
    if out.exists():
        raw = out.read_text(encoding="utf-8", errors="replace"); i = raw.find('{\n  "result"')
        if i >= 0:
            d = json.loads(raw[i:]); tokens = sum(c["usage"].get("total_tokens", 0) for c in d["calls"]); cost = d.get("cost_usd")
    total = len(ev[0]["payload"].get("chunks", []))
    acc = f"{len(fin['accepted'])}/{total}" if fin else "CRASHED"
    rejects = sum(1 for e in ev if e["type"] == "candidate.rejected" and "compile" not in (e["attempt_id"] or ""))
    print(f"{rid:<10}{(ev[0]['payload'].get('mode') or 'team')[:6]:<8}{acc:<10}{rejects:<14}{n('candidate.stale'):<7}{n('worker.question'):<11}{n('tool.probe_source'):<8}{n('decision.recorded'):<11}{int((ts[-1]-ts[0]).total_seconds()):<8}{tokens!s:<9}{cost}")
