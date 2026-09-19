import argparse, json, subprocess, time
from pathlib import Path

ap = argparse.ArgumentParser(); ap.add_argument("--cases"); ap.add_argument("--out"); ap.add_argument("--candidate"); ns = ap.parse_args()
exe = str(Path(ns.candidate) / "flow_target.exe")
with open(ns.out, "w", encoding="utf-8", newline="\n") as out:
    for line in open(ns.cases, encoding="utf-8"):
        c = json.loads(line); t = time.perf_counter()
        obs = {"schema_version": 1, "case_id": c["case_id"], "value": None, "error_code": None, "diagnostics": ""}
        try:
            p = subprocess.run([exe, c["export"], str(c["input"]["ts"]), str(c["input"]["width"])], capture_output=True, text=True, timeout=10)
            if p.returncode == 0:
                obs.update(status="ok", value=json.loads(p.stdout))
            else:
                obs.update(status="crash", diagnostics=p.stderr[-300:])
        except subprocess.TimeoutExpired:
            obs.update(status="timeout")
        obs["duration_ms"] = (time.perf_counter() - t) * 1000
        out.write(json.dumps(obs) + "\n")
