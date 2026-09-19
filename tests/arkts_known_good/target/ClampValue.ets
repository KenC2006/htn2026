export function clampValue(value: number, lower: number, upper: number): number {
  if (lower > upper) throw new Error('INVALID_BOUNDS');
  return Math.min(upper, Math.max(lower, value));
}
