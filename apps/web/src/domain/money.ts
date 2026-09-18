// Every fractional cent in the engine rounds half up through `roundHalfUp` (50% of $0.25 = $0.125 → $0.13).

export function roundHalfUp(numerator: number, denominator = 1): number {
  if (denominator <= 0) throw new RangeError("denominator must be positive");
  if (Number.isInteger(numerator) && Number.isInteger(denominator)) {
    return Math.floor((2 * numerator + denominator) / (2 * denominator));
  }
  return Math.floor(numerator / denominator + 0.5 + 1e-9);
}

/** Percent may carry up to two decimals (62.5%). */
export function percentOf(cents: number, percent: number): number {
  const basisPoints = Math.round(percent * 100);
  return roundHalfUp(cents * basisPoints, 10_000);
}

/** Largest-remainder split: integer parts that sum exactly to `total`. */
export function allocate(total: number, weights: number[]): number[] {
  if (weights.length === 0) return [];
  const safe = weights.map((w) => (Number.isFinite(w) && w > 0 ? w : 0));
  const sum = safe.reduce((a, b) => a + b, 0);
  const basis = sum > 0 ? safe : safe.map((_, i) => (i === 0 ? 1 : 0));
  const basisSum = sum > 0 ? sum : 1;
  const exact = basis.map((w) => (total * w) / basisSum);
  const parts = exact.map(Math.floor);
  let left = total - parts.reduce((a, b) => a + b, 0);
  const order = exact.map((v, i) => ({ i, frac: v - Math.floor(v) })).sort((a, b) => b.frac - a.frac || a.i - b.i);
  for (const { i } of order) {
    if (left <= 0) break;
    parts[i] += 1;
    left -= 1;
  }
  return parts;
}

export function clampCents(value: number): number {
  return Math.max(0, Math.round(value));
}
