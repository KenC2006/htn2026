export const SMOKE_MARKER: string = 'ARKTS_SMOKE_PASS';

export function runArkTSSmoke(): string {
  const values: number[] = [2, 3, 5];
  let total: number = 0;

  for (let index: number = 0; index < values.length; index++) {
    total += values[index];
  }

  if (total !== 10) {
    throw new Error(`ArkTS smoke assertion failed: expected 10, received ${total}`);
  }

  return `${SMOKE_MARKER}:${total}`;
}
