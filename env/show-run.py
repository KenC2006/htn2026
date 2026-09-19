"""Print a run's result and event trace: python env/show-run.py <run_id>"""
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1] / "runs"
rid = sys.argv[1]
out = root / f"{rid}.out"
if out.exists():
    raw = out.read_text(encoding="utf-8", errors="replace"); i = raw.find('{\n  "result"')
    if i >= 0:
        d = json.loads(raw[i:]); print("RESULT", json.dumps(d["result"]))
        print("calls", len(d["calls"]), "tokens", sum(c["usage"]["total_tokens"] for c in d["calls"]), "cost $%.5f" % sum(c["usage"].get("cost", 0) for c in d["calls"]))
    else:
        print(raw[-2500:])
for l in (root / rid / "events.jsonl").open(encoding="utf-8"):
    e = json.loads(l); p = e["payload"]
    print(e["sequence"], e["type"], e["chunk_id"] or "", e["attempt_id"] or p.get("label", ""), p.get("reason", ""), p.get("case_id") or "", (p.get("ruling") or "")[:110], p.get("affected_chunks") or "")
