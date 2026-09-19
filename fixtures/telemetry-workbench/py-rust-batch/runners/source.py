"""Frozen. Runs the original Python functions on the cases and records what they do."""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "legacy"))
import telemetry_batch  # noqa: E402


def plain(v):
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise TypeError(f"unsupported output type {type(v).__name__}")
    return v


ap = argparse.ArgumentParser(); ap.add_argument("--cases"); ap.add_argument("--out"); ns = ap.parse_args()
with open(ns.out, "w", encoding="utf-8", newline="\n") as out:
    for line in open(ns.cases, encoding="utf-8"):
        if not line.strip():
            continue
        c = json.loads(line); t = time.perf_counter()
        obs = {"schema_version": 1, "case_id": c["case_id"], "value": None, "error_code": None, "diagnostics": ""}
        try:
            args = dict(c["input"])
            args["records"] = [tuple(r) for r in args["records"]]  # JSON has no tuples; the original requires them
            try:
                result = getattr(telemetry_batch, c["export"])(**args)
            except Exception as e:  # the original's own, observable error
                obs.update(status="error", error_code=type(e).__name__)
            else:
                obs.update(status="ok", value=plain(result))
        except Exception as e:  # our problem, not the function's behavior
            obs.update(status="crash", diagnostics=f"{type(e).__name__}: {e}"[:300])
        obs["duration_ms"] = (time.perf_counter() - t) * 1000
        out.write(json.dumps(obs) + "\n")
