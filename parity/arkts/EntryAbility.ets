import { UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
//IMPORTS

class Observation {
  case_id: string = '';
  status: string = 'ok';
  value?: Object | null;
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
    const c = JSON.parse(text) as Record<string, Object>;
    const input = c['input'] as Record<string, Object>;
    const name = c['export'] as string;
    const o = new Observation();
    o.case_id = c['case_id'] as string;
    try {
      //ARMS
      else throw new Error('UNKNOWN_EXPORT');
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
