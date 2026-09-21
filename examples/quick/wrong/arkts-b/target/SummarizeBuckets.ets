import { bucketStart } from "./BucketStart";

export function summarizeBuckets(timestamps: number[], widthMs: number): number[][] {
  if (widthMs <= 0) {
    throw new Error("INVALID_WIDTH");
  }
  const counts = new Map<number, number>();
  for (const timestamp of timestamps) {
    const start = bucketStart(timestamp, widthMs);
    const prev = counts.get(start);
    counts.set(start, (prev === undefined ? 0 : prev) + 1);
  }
  const result: number[][] = [];
  for (const [start, count] of counts) {
    result.push([start, count]);
  }
  result.sort((a: number[], b: number[]) => a[0] - b[0]);
  return result;
}
