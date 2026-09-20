import type { DetectionSignals } from "../types";

export function scoreSignals(signals: DetectionSignals, base = 0.35): number {
  let score = base;
  if (signals.checksum) score += 0.4;
  if (signals.label) score += 0.2;
  if (signals.region) score += 0.05;
  if (signals.known) score = 0.99;
  return Math.round(Math.min(0.99, score) * 100) / 100;
}
