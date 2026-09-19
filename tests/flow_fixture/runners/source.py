import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "legacy"))
import timeparts

ap = argparse.ArgumentParser(); ap.add_argument("--cases"); ap.add_argument("--out"); ns = ap.parse_args()
with open(ns.out, "w", encoding="utf-8", newline="\n") as out:
    for line in open(ns.cases, encoding="utf-8"):
        c = json.loads(line); t = time.perf_counter()
        value = getattr(timeparts, c["export"])(c["input"]["ts"], c["input"]["width"])
        out.write(json.dumps({"schema_version": 1, "case_id": c["case_id"], "status": "ok", "value": value,
                              "error_code": None, "duration_ms": (time.perf_counter() - t) * 1000, "diagnostics": ""}) + "\n")
