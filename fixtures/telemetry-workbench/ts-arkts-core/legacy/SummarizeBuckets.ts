import { bucketStart } from './BucketStart';
export function summarizeBuckets(timestamps: number[], widthMs: number): number[][] {
  if (widthMs <= 0) throw new Error('INVALID_WIDTH');
  const counts = new Map<number, number>();
  for (const timestamp of timestamps) {
    const start = bucketStart(timestamp, widthMs);
    counts.set(start, (counts.get(start) || 0) + 1);
  }
  return Array.from(counts.entries()).sort((a, b) => a[0] - b[0]);
}
