"""Build outside gate workspaces; retain only a HAP and its receipt inside them."""
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
