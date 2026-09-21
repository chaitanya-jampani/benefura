import { charWeight } from "../detectors/text-stream";
import type { PageText, TextRun } from "../types";

export interface RunSpec {
  str: string;
  /** Points; defaults to one space width. */
  gap?: number;
}

const SIZE = 12;

export function textWidth(str: string, size = SIZE): number {
  let w = 0;
  for (const ch of str) w += (charWeight(ch) * size) / 1000;
  return w;
}

/** Lines are 18pt apart, starting at y=72. */
export function makePage(lines: Array<string | RunSpec[]>, opts: { pageIndex?: number; source?: PageText["source"] } = {}): PageText {
  const runs: TextRun[] = [];
  lines.forEach((line, li) => {
    const specs = typeof line === "string" ? [{ str: line, gap: 0 }] : line;
    let x = 72;
    specs.forEach((spec, ri) => {
      if (ri > 0) x += spec.gap ?? textWidth(" ");
      const advance = textWidth(spec.str);
      runs.push({
        str: spec.str,
        ox: x,
        oy: 72 + li * 18,
        dx: 1,
        dy: 0,
        advance,
        ascent: 0.9 * SIZE,
        descent: 0.2 * SIZE,
        hasEOL: ri === specs.length - 1,
      });
      x += advance;
    });
  });
  return { pageIndex: opts.pageIndex ?? 0, widthPt: 612, heightPt: 792, runs, source: opts.source ?? "pdf" };
}
