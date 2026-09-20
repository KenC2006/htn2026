"""Host-only ArkTS contract/protocol tests. Emulator checks are explicit CLI runs."""
import importlib.util
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from parity.engine import gate
from parity.engine.domain import in_domain

ROOT=Path(__file__).resolve().parents[1]
PROFILE=ROOT/'fixtures/telemetry-workbench/ts-arkts-core'
spec=importlib.util.spec_from_file_location('arkts_common',PROFILE/'runners/common.py')
common=importlib.util.module_from_spec(spec); spec.loader.exec_module(common)

def hold_lock(queue, release):
    with common.device_lock('parity-unit-test'):
        queue.put(True); release.wait(10)

class ArkTSProtocolTests(unittest.TestCase):
    case=dict(case_id='negative',chunk_id='T1')
    observation=dict(case_id='negative',status='ok',value=-10)

    def log(self,nonce='fresh'):
        payload=json.dumps(self.observation)
        return f'HiLog PARITY:{nonce}:0:2:{payload[:20]}\nHiLog PARITY:{nonce}:1:2:{payload[20:]}\nHiLog PARITY:{nonce}:DONE:1\n'

    def test_frames(self):
        self.assertEqual(common.frames(self.log(),'fresh',self.case),self.observation)

    def test_stale_logs_do_not_count(self):
        with self.assertRaises(ValueError): common.frames(self.log('old'),'fresh',self.case)

    def test_missing_frame(self):
        with self.assertRaises(ValueError): common.frames('\n'.join(self.log().splitlines()[1:]),'fresh',self.case)

    def test_duplicate_frame(self):
        with self.assertRaises(ValueError): common.frames(self.log()+self.log().splitlines()[0],'fresh',self.case)

    def test_duplicate_completion(self):
        with self.assertRaises(ValueError): common.frames(self.log()+'PARITY:fresh:DONE:1','fresh',self.case)

    def test_truncated_json(self):
        with self.assertRaises(ValueError): common.frames('PARITY:fresh:0:1:{\nPARITY:fresh:DONE:1','fresh',self.case)

    def test_unexpected_case(self):
        with self.assertRaises(ValueError): common.frames(self.log(),'fresh',dict(case_id='other',chunk_id='T1'))

    def test_bool_is_not_number(self):
        with self.assertRaises(ValueError): common.validate_observation(dict(status='ok',value=True),'T1')

    def test_wrong_summary_shape(self):
        with self.assertRaises(ValueError): common.validate_observation(dict(status='ok',value=[1,2]),'T3')

    def test_undeclared_error(self):
        with self.assertRaises(ValueError): common.validate_observation(dict(status='error',error_code='CRASH'),'T1')

    def test_device_lock_cross_process(self):
        queue=multiprocessing.Queue(); release=multiprocessing.Event()
        child=multiprocessing.Process(target=hold_lock,args=(queue,release)); child.start()
        try:
            self.assertTrue(queue.get(timeout=10))
            with self.assertRaises(TimeoutError):
                with common.device_lock('parity-unit-test',timeout=.2): pass
        finally:
            release.set(); child.join(10)
            if child.is_alive(): child.terminate(); child.join()
        with common.device_lock('parity-unit-test',timeout=.5): pass

    def test_all_generated_inputs_in_domain(self):
        manifests={p.stem:json.loads(p.read_text()) for p in (PROFILE/'chunks').glob('*.json')}
        for name in ['cases.jsonl','locked/cases.jsonl']:
            for case in common.read_cases(PROFILE/name):
                rules=manifests[case['chunk_id']]['input_domain']
                self.assertEqual(set(case['input']),set(rules))
                for key,value in case['input'].items():
                    self.assertEqual(in_domain(rules[key],value),'')

    def test_frozen_harness_tampering(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            fixture=Path(directory)/'fixture'; shutil.copytree(PROFILE,fixture)
            gate.freeze(fixture)
            (fixture/'harness/hvigorfile.ts').write_text('// changed')
            candidate=dict(files=[dict(path='target/BucketStart.ets',content='export function bucketStart(a: number,b: number): number { return 0; }')])
            verdict=gate.check(fixture,'T1',candidate,fixture/'cases.jsonl',Path(directory)/'run')
            self.assertEqual(verdict.status,'REJECTED_INTEGRITY')

    def test_candidate_cannot_edit_harness(self):
        with tempfile.TemporaryDirectory() as directory:
            verdict=gate.check(PROFILE,'T1',dict(files=[dict(path='harness/hvigorfile.ts',content='')]),PROFILE/'cases.jsonl',Path(directory))
            self.assertEqual(verdict.status,'REJECTED_POLICY')

if __name__=='__main__': unittest.main()
