import { UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { bucketStart } from '../core/BucketStart';
import { clampValue } from '../core/ClampValue';
import { summarizeBuckets } from '../core/SummarizeBuckets';

class CaseInput {
  timestampMs: number = 0;
  widthMs: number = 0;
  value: number = 0;
  lower: number = 0;
  upper: number = 0;
  timestamps: number[] = [];
}
class Case {
  case_id: string = '';
  chunk_id: string = '';
  input: CaseInput = new CaseInput();
}
class Observation {
  case_id: string = '';
  status: string = 'ok';
  value?: number | number[][];
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
      if (c.chunk_id === 'T1') o.value = bucketStart(c.input.timestampMs, c.input.widthMs);
      else if (c.chunk_id === 'T2') o.value = clampValue(c.input.value, c.input.lower, c.input.upper);
      else if (c.chunk_id === 'T3') o.value = summarizeBuckets(c.input.timestamps, c.input.widthMs);
      else throw new Error('UNKNOWN_CHUNK');
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
