"""Generate inputs only; expected observations always come from Node."""
import json
import random
from pathlib import Path

def generate(seed, count, prefix):
    rng = random.Random(seed)
    cases = []
    def add(chunk, value):
        cases.append(dict(case_id=f'{prefix}-{chunk}-{len(cases):04}', chunk_id=chunk, input=value))
    for t in [-1000000,-11,-10,-1,0,1,9,10,11,1000000]:
        for w in [-1,0,1,10,10000]:
            add('T1',dict(timestampMs=t,widthMs=w))
    for v,lo,hi in [(0,0,0),(-1,0,1),(2,0,1),(0,1,-1),(-1000000,-1000000,1000000)]:
        add('T2',dict(value=v,lower=lo,upper=hi))
    for ts in [[],[-1,0,1],[10,-10,10,-1,0],[-1000000,1000000]]:
        for w in [-1,0,1,10]:
            add('T3',dict(timestamps=ts,widthMs=w))
    for _ in range(count):
        add('T1',dict(timestampMs=rng.randint(-1000000,1000000),widthMs=rng.randint(-2,10000)))
        add('T2',dict(value=rng.randint(-1000000,1000000),lower=rng.randint(-1000000,1000000),upper=rng.randint(-1000000,1000000)))
        add('T3',dict(timestamps=[rng.randint(-1000000,1000000) for _ in range(rng.randint(0,32))],widthMs=rng.randint(-2,10000)))
    return cases

if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    for name, rows in [('named_cases.jsonl',generate(0,0,'named')),('cases.jsonl',generate(20260919,20,'dev')),('locked/cases.jsonl',generate(871231,25,'locked'))]:
        (root/name).parent.mkdir(parents=True,exist_ok=True)
        (root/name).write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
