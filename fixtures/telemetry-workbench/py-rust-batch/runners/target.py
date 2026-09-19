"""Frozen. Runs the rewritten code on the cases and records what it does."""
import argparse, json, subprocess, time
from pathlib import Path

ap = argparse.ArgumentParser(); ap.add_argument("--cases"); ap.add_argument("--out"); ap.add_argument("--candidate"); ns = ap.parse_args()
exe = str(Path(ns.candidate) / "parity_target.exe")
with open(ns.out, "w", encoding="utf-8", newline="\n") as out:
    for line in open(ns.cases, encoding="utf-8"):
        if not line.strip():
            continue
        c = json.loads(line); t = time.perf_counter()
        obs = {"schema_version": 1, "case_id": c["case_id"], "value": None, "error_code": None, "diagnostics": ""}
        try:
            p = subprocess.run([exe], input=json.dumps({"export": c["export"], "input": c["input"]}), capture_output=True,
                               text=True, encoding="utf-8", timeout=10)
            if p.returncode == 0:
                reply = json.loads(p.stdout)
                if "error" in reply:
                    obs.update(status="error", error_code=reply["error"])
                else:
                    obs.update(status="ok", value=reply["ok"])
            else:
                obs.update(status="crash", diagnostics=p.stderr[-300:])
        except subprocess.TimeoutExpired:
            obs.update(status="timeout")
        except Exception as e:
            obs.update(status="crash", diagnostics=f"{type(e).__name__}: {e}"[:300])
        obs["duration_ms"] = (time.perf_counter() - t) * 1000
        out.write(json.dumps(obs) + "\n")
