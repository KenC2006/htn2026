"""Frozen. Writes cases.jsonl (Aidan's 24 named cases + generated) and locked/cases.jsonl (hidden).

The input distribution is Aidan's: batch sizes weighted toward small, timestamps biased to bucket
boundaries and negatives, 30 % duplicate keys, a third of those with an equal sequence, values at the limits.
Run from this folder: python gen_cases.py
"""
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
BATCH_SIZES = (0, 1, 2, 3, 5, 8, 13, 50, 200)
BATCH_WEIGHTS = (4, 8, 10, 10, 12, 12, 12, 12, 8)
WIDTHS = (1, 7, 1000, 60_000, 3_600_000, 86_400_000)
SENSOR_IDS = ("s1", "s2", "s-3", "s_4", "S5", "sensor-06", "A", "z9")
TS_LIMIT, VALUE_LIMIT, SEQ_LIMIT = 10**12, 10**9, 2**31 - 1
P_DUP_KEY, P_EQUAL_SEQ = 0.30, 0.10
PIECES = (("P1", "dedupe_latest"), ("P2", "window_stats"), ("P3", "summarize"))


def _timestamp(rng, width):
    r = rng.random()
    if r < 0.10:
        ts = rng.randrange(0, TS_LIMIT // width + 1) * width          # exact multiple
    elif r < 0.20:
        ts = rng.randrange(0, TS_LIMIT // width + 1) * width - 1      # multiple - 1
    else:
        ts = rng.randrange(0, TS_LIMIT + 1)
    if rng.random() < 0.20:
        ts = -ts
    return max(-TS_LIMIT, min(TS_LIMIT, ts))


def _value(rng):
    r = rng.random()
    return VALUE_LIMIT if r < 0.05 else -VALUE_LIMIT if r < 0.10 else rng.randint(-VALUE_LIMIT, VALUE_LIMIT)


def _records(rng, n, width):
    if n == 0:
        return []
    pool = rng.sample(list(SENSOR_IDS), rng.randint(1, len(SENSOR_IDS)))
    out = []
    for _ in range(n):
        if out and rng.random() < P_DUP_KEY:
            sensor, ts, seq, _v = out[rng.randrange(len(out))]
            if rng.random() >= P_EQUAL_SEQ / P_DUP_KEY:
                seq = rng.randrange(0, SEQ_LIMIT + 1)
        else:
            sensor, ts, seq = pool[rng.randrange(len(pool))], _timestamp(rng, width), rng.randrange(0, SEQ_LIMIT + 1)
        out.append([sensor, ts, seq, _value(rng)])
    return out


def _boundary_records(rng, width):
    pool = rng.sample(list(SENSOR_IDS), rng.randint(1, 3))
    out = []
    for _ in range(rng.randint(1, 12)):
        m = -rng.randint(1, max(1, TS_LIMIT // width))
        ts = max(-TS_LIMIT, min(-1, m * width + rng.choice((-1, 0, 1))))
        out.append([pool[rng.randrange(len(pool))], ts, rng.randrange(0, 4), _value(rng)])
    return out


def _case(piece, export, case_id, records, width):
    inputs = {"records": records} if export == "dedupe_latest" else {"records": records, "width_ms": width}
    return {"schema_version": 1, "case_id": case_id, "chunk_id": piece, "export": export, "input": inputs}


def generate(seed, n, tag, boundary_first=0):
    rng, cases = random.Random(seed), []
    for i in range(n):
        piece, export = PIECES[i % 3]
        width = WIDTHS[rng.randrange(len(WIDTHS))]
        records = (_boundary_records(rng, width) if i < boundary_first
                   else _records(rng, rng.choices(BATCH_SIZES, weights=BATCH_WEIGHTS, k=1)[0], width))
        cases.append(_case(piece, export, f"{piece}-{tag}-{i:04d}", records, width))
    return cases


def write(path, cases):
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for c in cases:
            fh.write(json.dumps(c, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    named = [json.loads(line) for line in (HERE / "named_cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    write(HERE / "cases.jsonl", named + generate(20260919, 96, "dev"))
    write(HERE / "locked" / "cases.jsonl", generate(4242, 300, "locked", boundary_first=30))
    print(f"{len(named)} named + 96 generated visible cases, 300 hidden")
