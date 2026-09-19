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
        print("agent turns", len(d["calls"]), "tokens", sum(c["usage"].get("total_tokens", 0) for c in d["calls"]), "cost_usd", d.get("cost_usd"))
    else:
        print(raw[-3000:])
for l in (root / rid / "events.jsonl").open(encoding="utf-8"):
    e = json.loads(l); p = e["payload"]
    extra = p.get("question") or p.get("answer") or p.get("ruling") or p.get("detail") or (json.dumps(p.get("input")) + " -> " + p.get("result", "") if "input" in p else "") or p.get("result") or ""
    print(f"{e['sequence']:>3} {e['type']:<20} {e['actor']:<17} {e['chunk_id'] or '':<3} {p.get('reason', ''):<18} {p.get('case_id') or '':<16} {str(extra)[:130]}")
