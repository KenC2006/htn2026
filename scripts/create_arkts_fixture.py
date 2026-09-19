"""Reproducibly create the initial bounded ArkTS fixture (no expected answers)."""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'fixtures/telemetry-workbench/ts-arkts-core'

def write(name, value):
    path = P / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2) + '\n', encoding='utf-8')

def main():
    smoke = ROOT / 'experiments/arkts-smoke'
    for name in ['AppScope', 'entry/src/main/resources', 'hvigor']:
        shutil.copytree(smoke / name, P / 'harness' / name, dirs_exist_ok=True)
    for name in ['hvigorfile.ts', 'oh-package.json5', 'entry/hvigorfile.ts', 'entry/build-profile.json5', 'entry/oh-package.json5', 'entry/obfuscation-rules.txt', 'entry/src/main/module.json5']:
        write('harness/' + name, (smoke / name).read_text(encoding='utf-8'))
    write('harness/build-profile.json5', (smoke / 'build-profile.example.json5').read_text())
    app = json.loads((smoke / 'AppScope/app.json5').read_text())
    # Reuse the debug provision's bundle id; the test runner serializes device use.
    write('harness/AppScope/app.json5', app)
    module = json.loads((smoke / 'entry/src/main/module.json5').read_text())
    module['module'].pop('extensionAbilities', None)
    write('harness/entry/src/main/module.json5', module)
    write('profile.json', dict(schema_version=1, profile='ts-arkts-core', languages=dict(source='TypeScript', target='ArkTS'), run_source=['python','runners/source.py'], build_target=['python','runners/build.py'], run_target=['python','runners/target.py'], preflight=['python','runners/build.py','--preflight'], verify_seconds=300, frozen=['profile.json','chunks/*','contracts/*','legacy/*','runners/*','harness/*','cases.jsonl','named_cases.jsonl','locked/*','gen_cases.py'], oracle_paths=['legacy','cases.jsonl','named_cases.jsonl','locked','gen_cases.py'], locked_cases='locked/cases.jsonl', forbid_patterns=[dict(regex=r'\b(eval|Function|require|globalThis|console|hilog|Reflect|Proxy)\b', why='dynamic execution and platform access are excluded')]))
    numeric = dict(type='int', min=-1000000, max=1000000)
    width = dict(type='int', min=-2, max=10000)
    specs = [
        ('T1','BucketStart','bucketStart','timestampMs: number, widthMs: number','number',dict(timestampMs=numeric,widthMs=width),dict(timestampMs=-1,widthMs=10),[],['numeric-v1','bucket-v1']),
        ('T2','ClampValue','clampValue','value: number, lower: number, upper: number','number',dict(value=numeric,lower=numeric,upper=numeric),dict(value=12,lower=0,upper=10),[],['numeric-v1','clamp-v1']),
        ('T3','SummarizeBuckets','summarizeBuckets','timestamps: number[], widthMs: number','number[][]',dict(timestamps=dict(type='list[int]',min=-1000000,max=1000000,max_items=32),widthMs=width),dict(timestamps=[-1,0,1],widthMs=10),['T1'],['numeric-v1','bucket-v1'])]
    for cid, module, fn, args, result, domain, example, deps, contracts in specs:
        signature = f'export function {fn}({args}): {result}'
        write(f'chunks/{cid}.json',dict(schema_version=1,chunk_id=cid,profile='ts-arkts-core',source_files=[f'legacy/{module}.ts'],exports=[fn],write_allowlist=[f'target/{module}.ets'],depends_on=deps,contract_ids=contracts,worker_notes=signature + '. Pure synchronous numeric code only. Throw new Error("INVALID_WIDTH") for widthMs <= 0; clamp throws new Error("INVALID_BOUNDS") for lower > upper. Summary returns [bucketStart,count] pairs sorted by bucket start and must import { bucketStart } from "./BucketStart". No other imports, platform APIs, dynamic execution, or module-level effects.',limits=dict(attempts=3,verify_seconds_per_attempt=300),example_input=example,input_domain=domain))
        write(f'target/{module}.ets', signature + (' { return []; }\n' if result=='number[][]' else ' { return 0; }\n'))
    behaviors = {'numeric-v1':'Finite bounded integers only; no signed-zero distinction. No mutation or side effects. JSON numbers and arrays only.', 'bucket-v1':'Width <= 0 throws INVALID_WIDTH, including empty summaries. bucketStart = Math.floor(timestampMs / widthMs) * widthMs. summarizeBuckets calls bucketStart and returns [start,count] numeric pairs sorted ascending; empty input returns [].', 'clamp-v1':'If lower > upper throw INVALID_BOUNDS; otherwise return Math.min(upper, Math.max(lower, value)). Bounds are inclusive.'}
    for cid, behavior in behaviors.items():
        write(f'contracts/{cid}.json',dict(schema_version=1,contract_id=cid,version=1,behavior=behavior,guidance=[],evidence_refs=[]))

if __name__ == '__main__':
    main()
