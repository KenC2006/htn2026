"""Trusted host utilities; never imported by candidate core modules."""
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

def validate_observation(row, chunk):
    if row.get('status')=='error':
        if row.get('error_code') != ('INVALID_BOUNDS' if chunk=='T2' else 'INVALID_WIDTH') or 'value' in row:
            raise ValueError('undeclared API error')
    elif row.get('status')=='ok':
        value=row.get('value')
        def number(v):
            return type(v) in (int,float) and abs(v)<=9007199254740991 and v==int(v)
        if chunk=='T3':
            valid=isinstance(value,list) and all(isinstance(p,list) and len(p)==2 and all(number(v) for v in p) for p in value)
        else:
            valid=number(value)
        if not valid or 'error_code' in row:
            raise ValueError('invalid numeric observation shape')
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
    validate_observation(row,case['chunk_id'])
    return row
