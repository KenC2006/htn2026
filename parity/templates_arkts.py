"""Files every generated TypeScript -> ArkTS project shares, plus the small per-project generators.

Mirrors parity/templates.py's split for Python -> Rust: most of the runner code is identical across
every generated project (frozen, copied verbatim, like Rust's json.rs/source.py/target.py), because
`runners/common.py`, `build.py`, `target.py`, `signing.cjs`, `source.py`, `source_runner.cjs` and
`policy.cjs` were already written generically in the hand-authored `ts-arkts-core` fixture (see
`fixtures/telemetry-workbench/ts-arkts-core/`) or become generic here by reading one small per-project
data file, `runners/dispatch.json`, instead of hardcoding chunk/function names. Only three things are
generated per project: `dispatch.json` itself, `entryability/EntryAbility.ets` (ArkTS needs static
imports and typed fields; it cannot read JSON at declaration time), and each `target/<fn>.ets`
placeholder.
"""
from __future__ import annotations

import json

# ── runners/common.py: trusted host utilities. Identical to ts-arkts-core's, except
# validate_observation reads the shape/error codes for a chunk from dispatch.json instead of
# hardcoding T1/T2/T3, so the exact same file works for any generated project.
COMMON_PY = r'''"""Frozen. Trusted host utilities; never imported by candidate core modules."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

HOME = Path(os.environ.get('PARITY_DEVECO_HOME', r'C:\Program Files\Huawei\DevEco Studio'))
NODE = Path(os.environ.get('PARITY_NODE', str(HOME/'tools/node/node.exe')))
TS = Path(os.environ.get('PARITY_TYPESCRIPT', str(HOME/'tools/ohpm/node_modules/typescript/lib/typescript.js')))
HDC = HOME/'sdk/default/openharmony/toolchains/hdc.exe'

def run(args, *, cwd=None, timeout=120, env=None):
    proc = subprocess.Popen([str(a) for a in args],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    try:
        output = proc.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
        else:
            proc.kill()
        proc.communicate()
        raise RuntimeError('tool timed out') from None
    if proc.returncode:
        raise RuntimeError(output[-4000:])
    return output

def device():
    targets = [s.strip() for s in run([HDC,'list','targets']).splitlines() if s.strip() and not s.startswith('[')]
    chosen = os.environ.get('PARITY_HDC_TARGET')
    if chosen in targets:
        return chosen
    if not chosen and len(targets)==1:
        return targets[0]
    raise RuntimeError('Connect one DevEco emulator or set PARITY_HDC_TARGET to a connected device')

@contextlib.contextmanager
def device_lock(target, timeout=60):
    # OS locks release on process termination; lock files are never unlinked (avoids inode races).
    path = Path(tempfile.gettempdir()) / ('parity-hdc-'+hashlib.sha256(target.encode()).hexdigest()+'.lock')
    with path.open('a+b') as stream:
        try:
            # Seed one byte so a lock on it can be taken. On Windows this read
            # itself raises PermissionError while another process holds the
            # mandatory region lock; that failure means the file is already
            # non-empty, so there is nothing to seed.
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'0'); stream.flush()
        except PermissionError:
            pass
        deadline=time.monotonic()+timeout
        while True:
            try:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except (OSError,BlockingIOError):
                if time.monotonic()>=deadline:
                    raise TimeoutError('device lock timed out')
                time.sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name=='nt':
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(stream,fcntl.LOCK_UN)

def read_cases(path):
    rows=[json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]
    ids=[r['case_id'] for r in rows]
    if not rows or len(set(ids))!=len(ids):
        raise ValueError('empty or duplicate case inventory')
    return rows

def _dispatch():
    return json.loads((Path(__file__).resolve().with_name('dispatch.json')).read_text(encoding='utf-8'))['functions']

def _matches_shape(value, shape):
    if shape=='number':
        import math
        return type(value) in (int,float) and not isinstance(value,bool) and math.isfinite(value)
    if shape=='string':
        return isinstance(value,str)
    if shape=='boolean':
        return isinstance(value,bool)
    if shape.endswith('[]'):
        inner=shape[:-2]
        return isinstance(value,list) and all(_matches_shape(v,inner) for v in value)
    return False

def validate_observation(row, export):
    meta = _dispatch()[export]
    if row.get('status')=='error':
        if row.get('error_code') not in meta['errors'] or 'value' in row:
            raise ValueError('undeclared API error')
    elif row.get('status')=='ok':
        if not _matches_shape(row.get('value'), meta['returns']) or 'error_code' in row:
            raise ValueError('invalid observation shape')
    else:
        raise ValueError('crash or malformed observation')

def frames(text, nonce, case):
    prefix=f'PARITY:{nonce}:'
    pieces={}
    total=None
    done=0
    for line in text.splitlines():
        if prefix not in line:
            continue
        payload=line.split(prefix,1)[1]
        if payload=='DONE:1':
            done+=1
            continue
        seq,count,part=payload.split(':',2)
        seq,count=int(seq),int(count)
        if count<1 or count>100 or seq<0 or seq>=count or seq in pieces or (total is not None and total!=count):
            raise ValueError('duplicate or invalid frame')
        total=count; pieces[seq]=part
    if done!=1 or total is None or set(pieces)!=set(range(total)):
        raise ValueError('missing frames or completion')
    row=json.loads(''.join(pieces[i] for i in range(total)))
    if row.get('case_id')!=case['case_id']:
        raise ValueError('unexpected case id')
    validate_observation(row,case['export'])
    return row
'''

# runners/build.py, target.py, signing.cjs: byte-identical to ts-arkts-core's. Already fully
# generic (no function/chunk names anywhere); proven end to end on the real emulator there.
BUILD_PY = r'''"""Frozen. Build outside gate workspaces; retain only a HAP and its receipt inside them."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from common import HOME, NODE, TS, HDC, run, device

def signing():
    path=os.environ.get('PARITY_ARKTS_SIGNING_PROFILE')
    if not path:
        for parent in Path(__file__).resolve().parents:
            local=parent/'experiments/arkts-smoke/build-profile.json5'
            if local.is_file():
                path=str(local); break
    if not path or not Path(path).is_file():
        raise RuntimeError('Set PARITY_ARKTS_SIGNING_PROFILE to a trusted local DevEco build-profile.json5 with absolute signing paths')
    resolved=Path(path).resolve()
    # Read via JSON5 without emitting credential-bearing configuration.
    run([NODE,Path(__file__).with_name('signing.cjs'),HOME,resolved])
    return resolved

def preflight():
    for path in [NODE,TS,HDC,HOME/'jbr/bin/java.exe',HOME/'tools/hvigor/hvigor/bin/hvigor.js']:
        if not path.is_file():
            raise RuntimeError(f'Missing DevEco component: {path}')
    signing()
    print('DevEco target: '+device())

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--preflight',action='store_true'); parser.add_argument('--unsigned',action='store_true')
    args=parser.parse_args()
    if args.preflight:
        preflight(); return
    root=Path.cwd()
    run([NODE,root/'runners/policy.cjs',TS,root/'target'])
    env=os.environ.copy()
    env.update(DEVECO_SDK_HOME=str(HOME/'sdk'),JAVA_HOME=str(HOME/'jbr'),NODE_HOME=str(NODE.parent))
    env['PATH']=str(NODE.parent)+os.pathsep+str(HOME/'jbr/bin')+os.pathsep+env.get('PATH','')
    env.pop('OHOS_BASE_SDK_HOME',None); env.pop('OHOS_SDK_HOME',None)
    with tempfile.TemporaryDirectory(prefix='parity-arkts-build-') as directory:
        stage=Path(directory)
        shutil.copytree(root/'harness',stage,dirs_exist_ok=True)
        shutil.copytree(root/'target',stage/'entry/src/main/ets/core')
        if not args.unsigned:
            shutil.copyfile(signing(),stage/'build-profile.json5')
        (stage/'local.properties').write_text('sdk.dir='+str(HOME/'sdk').replace('\\','/')+'\n')
        # Junctions exist ONLY outside candidate trees and are detached before cleanup.
        modules=stage/'node_modules/@ohos'; modules.mkdir(parents=True)
        links=[]
        try:
            for name in ['hvigor','hvigor-ohos-plugin']:
                link=modules/name
                escaped=lambda p: str(p).replace("'","''")
                run(['powershell','-NoProfile','-Command',f"New-Item -ItemType Junction -Path '{escaped(link)}' -Target '{escaped(HOME/'tools/hvigor'/name)}' | Out-Null"])
                links.append(link)
            env['NODE_PATH']=str(stage/'node_modules')
            try:
                output=run([NODE,HOME/'tools/hvigor/hvigor/bin/hvigor.js','--mode','module','-p','product=default','assembleHap','--no-daemon'],cwd=stage,env=env,timeout=220)
            except RuntimeError as error:
                # Signing output can contain credentials; never return raw signed-build logs.
                if not args.unsigned:
                    raise RuntimeError('DevEco build failed. Run --unsigned for compiler diagnostics; inspect signing in DevEco.') from None
                raise error
            haps=list((stage/'entry/build/default/outputs/default').glob('*-unsigned.hap' if args.unsigned else '*-signed.hap'))
            if len(haps)!=1:
                raise RuntimeError('Expected one '+('unsigned' if args.unsigned else 'signed')+' HAP')
            dest=root/'_arkts.hap'; shutil.copyfile(haps[0],dest)
            receipt=dict(hap_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),unsigned=args.unsigned,targets={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'target').glob('*.ets')})
            (root/'_arkts_artifact.json').write_text(json.dumps(receipt),encoding='utf-8')
            print('ArkTS build OK; HAP sha256='+receipt['hap_sha256'])
        finally:
            for link in links:
                # rmdir on a junction removes the junction, never its installed-tool target.
                os.rmdir(link)

if __name__=='__main__':
    main()
'''

TARGET_PY = r'''"""Frozen. Runs the rewritten ArkTS code on the cases and records what it does."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from common import HDC, run, device, device_lock, read_cases, frames

def main():
    parser=argparse.ArgumentParser()
    for name in ['cases','out','candidate']:
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    root=Path(args.candidate)
    artifact=json.loads((root/'_arkts_artifact.json').read_text())
    hap=root/'_arkts.hap'
    if artifact['unsigned'] or hashlib.sha256(hap.read_bytes()).hexdigest()!=artifact['hap_sha256']:
        raise ValueError('Unsigned or mismatched artifact')
    if artifact['targets']!={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'target').glob('*.ets')}:
        raise ValueError('Candidate changed after build')
    cases=read_cases(args.cases)
    target=device(); observations=[]
    bundle=json.loads((root/'harness/AppScope/app.json5').read_text())['app']['bundleName']
    def hdc(*args):
        text=run([HDC,'-t',target,*args],timeout=25)
        if '[Fail]' in text or 'error:' in text.lower():
            raise RuntimeError(text[-1000:])
        return text
    with device_lock(target):
        try:
            if 'success' not in hdc('install','-r',hap).lower():
                raise RuntimeError('HDC did not confirm installation')
        except RuntimeError as error:
            # A stale install signed with different debug material (e.g. from another
            # checkout) blocks '-r' replace. Uninstall the bundle once and retry.
            if 'sign info inconsistent' not in str(error).lower():
                raise
            run([HDC,'-t',target,'uninstall',bundle],timeout=25)
            if 'success' not in hdc('install','-r',hap).lower():
                raise RuntimeError('HDC did not confirm installation after uninstall retry') from None
        with tempfile.TemporaryDirectory(prefix='parity-arkts-log-') as directory:
            log=Path(directory)/'hilog.txt'
            with log.open('w',encoding='utf-8') as stream:
                capture=subprocess.Popen([str(HDC),'-t',target,'hilog'],stdout=stream,stderr=subprocess.STDOUT)
                try:
                    for case in cases:
                        nonce=uuid.uuid4().hex
                        # Hex ASCII avoids host/device shell quoting ambiguities. One case per launch bounds payloads.
                        payload=json.dumps(case,separators=(',',':')).encode().hex()
                        hdc('shell','aa','force-stop',bundle)
                        result=hdc('shell','aa','start','-a','EntryAbility','-b',bundle,'--ps','parityNonce',nonce,'--ps','parityCase',payload)
                        if 'start ability successfully' not in result:
                            raise RuntimeError('Ability launch not confirmed')
                        deadline=time.monotonic()+15
                        while True:
                            text=log.read_text(encoding='utf-8',errors='replace')
                            if f'PARITY:{nonce}:DONE:' in text:
                                observations.append(frames(text,nonce,case)); break
                            if capture.poll() is not None or time.monotonic()>deadline:
                                raise RuntimeError('Missing fresh ArkTS completion; capture ended or device timed out')
                            time.sleep(.05)
                finally:
                    capture.terminate()
                    try: capture.wait(timeout=5)
                    except subprocess.TimeoutExpired: capture.kill(); capture.wait()
    Path(args.out).write_text(''.join(json.dumps(o)+'\n' for o in observations),encoding='utf-8')
    print(json.dumps(dict(device=target,hap_sha256=artifact['hap_sha256'],cases=len(cases),input_sha256=hashlib.sha256(Path(args.cases).read_bytes()).hexdigest())))

if __name__=='__main__':
    main()
'''

SIGNING_CJS = r'''const fs=require('fs');
const path=require('path');
const home=process.argv[2];
const JSON5=require(path.join(home,'tools/hvigor/hvigor-ohos-plugin/node_modules/json5'));
const p=JSON5.parse(fs.readFileSync(process.argv[3],'utf8'));
const product=p.app.products.find(x=>x.name==='default');
const signing=p.app.signingConfigs.find(x=>x.name===product.signingConfig);
if(!signing) throw Error('No signing configuration selected for default product; apply signing in DevEco');
for(const key of ['certpath','profile','storeFile']) {
  const value=signing.material[key];
  if(!value || !path.isAbsolute(value) || !fs.existsSync(value)) throw Error('Signing material must reference existing absolute paths; configure in DevEco');
}
'''

# runners/source.py: also already generic in ts-arkts-core (delegates to source_runner.cjs).
SOURCE_PY = r'''"""Frozen. Runs the original TypeScript functions on the cases and records what they do."""
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
        validate_observation(row,case['export'])

if __name__=='__main__':
    main()
'''

# runners/source_runner.cjs: generalized from ts-arkts-core's. It no longer hardcodes function or
# chunk names; it reads runners/dispatch.json (module name + per-function params/errors) instead,
# dispatching by the case's `export` field (present on every case per CONTRACTS.md), so this file
# is identical across every generated project too.
SOURCE_RUNNER_CJS = r'''const fs = require('fs');
const path = require('path');
const [compiler, source, outDir, casesPath, output] = process.argv.slice(2);
const ts = require(compiler);
const dispatch = JSON.parse(fs.readFileSync(path.join(__dirname, 'dispatch.json'), 'utf8'));
const files = fs.readdirSync(source).filter(x => x.endsWith('.ts')).map(x => path.join(source, x));
const program = ts.createProgram(files, { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, strict: true, outDir });
const diagnostics = ts.getPreEmitDiagnostics(program);
if (diagnostics.length) throw Error(ts.formatDiagnosticsWithColorAndContext(diagnostics, { getCanonicalFileName: x => x, getCurrentDirectory: () => source, getNewLine: () => '\n' }));
program.emit();
const mod = require(path.join(outDir, dispatch.module + '.js'));
const rows = fs.readFileSync(casesPath, 'utf8').trim().split(/\r?\n/).filter(Boolean).map(JSON.parse).map(c => {
  const meta = dispatch.functions[c.export];
  if (!meta) throw Error('UNKNOWN_EXPORT');
  const fn = mod[c.export];
  const args = meta.params.map(p => c.input[p.name]);
  // Every thrown message is recorded as a declared error, the same way runners/source.py does
  // for Python: the declared error set is *derived* from what the original really does (parity
  // new / newproject_ts.py), not enforced here. Downstream, common.py::validate_observation
  // checks a *candidate's* observations against that derived set; the oracle itself is trusted.
  try {
    return { case_id: c.case_id, status: 'ok', value: fn(...args) };
  } catch (e) {
    return { case_id: c.case_id, status: 'error', error_code: e.message };
  }
});
fs.writeFileSync(output, rows.map(x => JSON.stringify(x)).join('\n') + '\n');
'''

# runners/policy.cjs: generalized the same way. Reads dispatch.json for which target/*.ets file
# belongs to which export and which single relative import (if any) that function's dependency
# edge approves, instead of one hand-written check per fixture function.
POLICY_CJS = r'''// Parse candidates before DevEco executes build tooling. This is a scope policy, not an OS sandbox.
const fs = require('fs');
const path = require('path');
const ts = require(process.argv[2]);
const root = process.argv[3];
const dispatch = JSON.parse(fs.readFileSync(path.join(__dirname, 'dispatch.json'), 'utf8'));
const forbidden = new Set(['eval', 'Function', 'require', 'globalThis', 'console', 'hilog', 'Reflect', 'Proxy', 'Date', 'setTimeout', 'setInterval', 'fetch']);
for (const exportName of Object.keys(dispatch.functions)) {
  const meta = dispatch.functions[exportName];
  const file = meta.file;
  const source = ts.createSourceFile(file, fs.readFileSync(path.join(root, file), 'utf8'), ts.ScriptTarget.Latest, true);
  if (source.parseDiagnostics.length) throw Error('Candidate parse failed: ' + file);
  const allowed = meta.allowedImports || [];
  for (const statement of source.statements) {
    if (ts.isImportDeclaration(statement)) {
      const spec = statement.moduleSpecifier.text;
      const clause = statement.importClause;
      const bindings = clause && clause.namedBindings;
      const single = bindings && ts.isNamedImports(bindings) && bindings.elements.length === 1 ? bindings.elements[0] : null;
      const ok = !!(clause && !clause.name && single && !single.propertyName && allowed.some(a => a.from === spec && a.name === single.name.text));
      if (!ok) throw Error('Unapproved candidate import in ' + file);
    } else if (!ts.isFunctionDeclaration(statement)) {
      throw Error('Only function declarations and approved imports are permitted in ' + file);
    }
  }
  function visit(node) {
    if (node.kind === ts.SyntaxKind.ImportKeyword || ts.isExportDeclaration(node) ||
        (ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression)) ||
        (ts.isIdentifier(node) && forbidden.has(node.text))) {
      throw Error('Forbidden dynamic execution or platform access in ' + file);
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}
'''

# ── generated per project: EntryAbility.ets ──────────────────────────────────
_ENTRY_ABILITY_TEMPLATE = r'''import { UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
__PARITY_IMPORTS__
class CaseInput {
__PARITY_FIELDS__
}
class Case {
  case_id: string = '';
  export: string = '';
  input: CaseInput = new CaseInput();
}
class Observation {
  case_id: string = '';
  status: string = 'ok';
  value?: __PARITY_VALUE_TYPE__;
  error_code?: string;
}
export default class EntryAbility extends UIAbility {
  onCreate(want: Want): void {
    if (!want.parameters) return;
    const nonce = want.parameters['parityNonce'] as string;
    const hex = want.parameters['parityCase'] as string;
    if (!nonce || !hex) return;
    let text: string = '';
    for (let i: number = 0; i < hex.length; i += 2) {
      text += String.fromCharCode(parseInt(hex.substring(i, i + 2), 16));
    }
    const c: Case = JSON.parse(text) as Case;
    const o = new Observation();
    o.case_id = c.case_id;
    try {
__PARITY_DISPATCH__
    } catch (e) {
      o.status = 'error';
      o.error_code = (e as Error).message;
    }
    const output: string = JSON.stringify(o);
    const count: number = Math.ceil(output.length / 400);
    for (let seq: number = 0; seq < count; seq++) {
      hilog.info(0, 'ParityArkTS', '%{public}s', 'PARITY:' + nonce + ':' + seq + ':' + count + ':' + output.substring(seq * 400, (seq + 1) * 400));
    }
    hilog.info(0, 'ParityArkTS', '%{public}s', 'PARITY:' + nonce + ':DONE:1');
  }
}
'''


def _zero_field(kind: str) -> str:
    if kind.endswith("[]"):
        return "[]"
    return {"number": "0", "string": "''", "boolean": "false"}[kind]


def _zero_return(kind: str) -> str:
    if kind.endswith("[]"):
        return "[]"
    return {"number": "0", "string": "''", "boolean": "false"}[kind]


def entry_ability_ets(functions: dict) -> str:
    """functions: export name -> {file, params: [{name, type}], returns}."""
    fields: dict[str, str] = {}
    for meta in functions.values():
        for p in meta["params"]:
            fields.setdefault(p["name"], p["type"])
    field_lines = "\n".join(f"  {name}: {kind} = {_zero_field(kind)};" for name, kind in fields.items())
    imports = "\n".join(f"import {{ {name} }} from '../core/{meta['file'][:-4]}';" for name, meta in functions.items())
    value_type = " | ".join(sorted({meta["returns"] for meta in functions.values()}))
    arms = []
    for name, meta in functions.items():
        args = ", ".join(f"c.input.{p['name']}" for p in meta["params"])
        keyword = "if" if not arms else "else if"
        arms.append(f"      {keyword} (c.export === '{name}') o.value = {name}({args});")
    arms.append("      else throw new Error('UNKNOWN_EXPORT');")
    dispatch = "\n".join(arms)
    return (_ENTRY_ABILITY_TEMPLATE
            .replace("__PARITY_IMPORTS__", imports)
            .replace("__PARITY_FIELDS__", field_lines)
            .replace("__PARITY_VALUE_TYPE__", value_type)
            .replace("__PARITY_DISPATCH__", dispatch))


def placeholder_ets(name: str, params: list[dict], returns: str) -> str:
    """A compiling but wrong ArkTS placeholder for one exported function, the ArkTS analog of
    newproject.py's Rust placeholders: it must build; being wrong is what makes the gate reject it."""
    sig = ", ".join(f"{p['name']}: {p['type']}" for p in params)
    return f"export function {name}({sig}): {returns} {{\n  return {_zero_return(returns)}; // placeholder\n}}\n"
