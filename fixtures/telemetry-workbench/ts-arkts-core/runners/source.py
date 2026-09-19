import argparse
import json
from pathlib import Path
import tempfile
from common import NODE, TS, run, read_cases, validate_observation

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cases',required=True); parser.add_argument('--out',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    cases=read_cases(args.cases)
    with tempfile.TemporaryDirectory(prefix='parity-ts-') as staging:
        run([NODE,root/'runners/source_runner.cjs',TS,root/'legacy',staging,Path(args.cases).resolve(),Path(args.out).resolve()])
    rows=read_cases(args.out)
    if [r['case_id'] for r in rows]!=[c['case_id'] for c in cases]:
        raise ValueError('oracle inventory mismatch')
    for row,case in zip(rows,cases):
        validate_observation(row,case['chunk_id'])

if __name__=='__main__':
    main()
