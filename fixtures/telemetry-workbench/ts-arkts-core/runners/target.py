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
