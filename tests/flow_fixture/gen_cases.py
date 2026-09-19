"""Seeded case generator. Locked set:  python gen_cases.py --seed 9191 --per-chunk 100 --prefix locked > locked/cases.jsonl

Inputs only. Expected outputs always come from running the original implementation at check time.
"""
import argparse, json, random

EXPORTS = (("S1", "bucket"), ("S2", "offset"), ("S3", "split"))
LIMIT, MAX_WIDTH = 10**12, 86_400_000

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, required=True)
ap.add_argument("--per-chunk", type=int, default=100)
ap.add_argument("--prefix", default="gen")
ns = ap.parse_args()
rng = random.Random(ns.seed)


def one():
    width = rng.choice([1, 2, 7, 1000, 60_000, MAX_WIDTH, rng.randint(1, MAX_WIDTH)])
    kind = rng.random()
    if kind < 0.35:                       # right at a bucket boundary, either side, mostly negative
        k = rng.randint(-LIMIT // width, LIMIT // width)
        ts = k * width + rng.choice([-1, 0, 1])
    elif kind < 0.5:                      # the ends of the domain
        ts = rng.choice([-LIMIT, LIMIT, -LIMIT + 1, LIMIT - 1, 0, -1, 1])
    else:
        ts = rng.randint(-LIMIT, LIMIT)
    return {"ts": max(-LIMIT, min(LIMIT, ts)), "width": width}


for chunk_id, export in EXPORTS:
    for i in range(ns.per_chunk):
        print(json.dumps({"schema_version": 1, "case_id": f"{ns.prefix}-{export}-{i:03d}", "chunk_id": chunk_id,
                          "export": export, "input": one()}))
