export function bucketStart(timestampMs: number, widthMs: number): number {
  if (widthMs <= 0) throw new Error('INVALID_WIDTH');
  return Math.floor(timestampMs / widthMs) * widthMs;
}
