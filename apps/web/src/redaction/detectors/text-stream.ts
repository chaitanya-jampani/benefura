import type { PageText, Rect, TextRun } from "../types";

// Helvetica widths per 1000 em: close enough to place characters in a run without real font metrics.
const WIDTHS: Record<string, number> = {
  " ": 278, "!": 278, '"': 355, "#": 556, $: 556, "%": 889, "&": 667, "'": 191, "(": 333, ")": 333,
  "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584,
  ">": 584, "?": 556, "@": 1015, "[": 278, "\\": 278, "]": 278, _: 556, "|": 260,
  A: 667, B: 667, C: 722, D: 722, E: 667, F: 611, G: 778, H: 722, I: 278, J: 500, K: 667, L: 556,
  M: 833, N: 722, O: 778, P: 667, Q: 778, R: 722, S: 667, T: 611, U: 722, V: 667, W: 944, X: 667,
  Y: 667, Z: 611, a: 556, b: 556, c: 500, d: 556, e: 556, f: 278, g: 556, h: 556, i: 222, j: 222,
  k: 500, l: 222, m: 833, n: 556, o: 556, p: 556, q: 556, r: 333, s: 500, t: 278, u: 556, v: 500,
  w: 722, x: 500, y: 500, z: 500,
};

export function charWeight(ch: string): number {
  return WIDTHS[ch] ?? (/\d/.test(ch) ? 556 : 600);
}

export interface CharRef {
  run: number;
  index: number;
}

export interface TextStream {
  page: PageText;
  /** Reading order; "\n" between lines, "\t" at wide column gaps. */
  text: string;
  /** Source run character per `text` character; null for inserted separators. */
  refs: Array<CharRef | null>;
  runLine: number[];
}

const WHITESPACE = /\s/;

function runSize(run: TextRun): number {
  return Math.max(0.5, run.ascent + run.descent);
}

function directionBucket(run: TextRun): number {
  return Math.round(Math.atan2(run.dy, run.dx) / (Math.PI / 4));
}

/** Baseline offset along the text's own "up" (dy, -dx); larger is higher. */
function perp(run: TextRun): number {
  return run.ox * run.dy - run.oy * run.dx;
}

function along(run: TextRun): number {
  return run.ox * run.dx + run.oy * run.dy;
}

export function buildTextStream(page: PageText): TextStream {
  const runs = page.runs;
  // pdf.js fills table-cell gaps with whitespace-only runs; dropping them keeps the gap visible as a column break.
  const indices = runs.map((_, i) => i).filter((i) => runs[i].str.trim().length > 0);

  const groups = new Map<number, number[]>();
  for (const i of indices) {
    const key = directionBucket(runs[i]);
    const list = groups.get(key) ?? [];
    list.push(i);
    groups.set(key, list);
  }
  const orderedGroups = [...groups.entries()].sort(([a], [b]) => Math.abs(a) - Math.abs(b) || a - b);

  const lines: number[][] = [];
  for (const [, members] of orderedGroups) {
    members.sort((a, b) => perp(runs[b]) - perp(runs[a]) || along(runs[a]) - along(runs[b]));
    let current: number[] = [];
    let ref = 0;
    let refSize = 0;
    for (const i of members) {
      const p = perp(runs[i]);
      const size = runSize(runs[i]);
      if (current.length > 0 && Math.abs(ref - p) <= 0.5 * Math.min(refSize, size)) {
        current.push(i);
      } else {
        if (current.length) lines.push(current);
        current = [i];
        ref = p;
        refSize = size;
      }
    }
    if (current.length) lines.push(current);
  }

  const runLine = new Array<number>(runs.length).fill(-1);
  let text = "";
  const refs: Array<CharRef | null> = [];
  const push = (s: string, ref: CharRef | null) => {
    text += s;
    refs.push(ref);
  };

  lines.forEach((line, lineId) => {
    line.sort((a, b) => along(runs[a]) - along(runs[b]));
    if (lineId > 0) push("\n", null);
    let prev: TextRun | null = null;
    for (const i of line) {
      const run = runs[i];
      if (prev) {
        const gap = along(run) - (along(prev) + prev.advance);
        const em = Math.max(runSize(prev), runSize(run));
        // Some PDFs fake bold by drawing the same run twice with a tiny offset.
        if (run.str === prev.str && Math.abs(along(run) - along(prev)) < 0.2 * em) continue;
        const touching = WHITESPACE.test(prev.str.at(-1) ?? "") || WHITESPACE.test(run.str[0] ?? "");
        if (gap > 1.8 * em) push("\t", null);
        else if (gap > 0.12 * em && !touching) push(" ", null);
      }
      runLine[i] = lineId;
      for (let c = 0; c < run.str.length; c++) {
        const ch = run.str[c];
        push(WHITESPACE.test(ch) ? " " : ch, { run: i, index: c });
      }
      prev = run;
    }
  });

  return { page, text, refs, runLine };
}

const prefixCache = new WeakMap<TextRun, Float64Array>();

function prefixWidths(run: TextRun): Float64Array {
  let prefix = prefixCache.get(run);
  if (!prefix) {
    prefix = new Float64Array(run.str.length + 1);
    for (let i = 0; i < run.str.length; i++) prefix[i + 1] = prefix[i] + charWeight(run.str[i]);
    prefixCache.set(run, prefix);
  }
  return prefix;
}

/** Page-point bounds of run characters [i0, i1). */
export function runSliceBounds(run: TextRun, i0: number, i1: number) {
  const prefix = prefixWidths(run);
  const total = prefix[run.str.length] || 1;
  const s0 = (prefix[i0] / total) * run.advance;
  const s1 = (prefix[i1] / total) * run.advance;
  const ux = run.dy;
  const uy = -run.dx;
  const xs: number[] = [];
  const ys: number[] = [];
  for (const s of [s0, s1]) {
    const bx = run.ox + run.dx * s;
    const by = run.oy + run.dy * s;
    xs.push(bx + ux * run.ascent, bx - ux * run.descent);
    ys.push(by + uy * run.ascent, by - uy * run.descent);
  }
  return {
    x0: Math.min(...xs),
    y0: Math.min(...ys),
    x1: Math.max(...xs),
    y1: Math.max(...ys),
    size: runSize(run),
  };
}

/** Page-fraction rectangles for stream range [start, end), one per line. */
export function rangeRects(stream: TextStream, start: number, end: number): Array<Rect & { lineHeightPt: number }> {
  const { page, refs, runLine } = stream;
  const byLine = new Map<number, { x0: number; y0: number; x1: number; y1: number; size: number }>();
  let i = Math.max(0, start);
  const stop = Math.min(end, refs.length);
  while (i < stop) {
    const ref = refs[i];
    if (!ref) {
      i++;
      continue;
    }
    let j = i + 1;
    let last = ref.index;
    while (j < stop && refs[j] && refs[j]!.run === ref.run && refs[j]!.index === last + 1) {
      last = refs[j]!.index;
      j++;
    }
    const b = runSliceBounds(page.runs[ref.run], ref.index, last + 1);
    const line = runLine[ref.run];
    const acc = byLine.get(line);
    if (acc) {
      acc.x0 = Math.min(acc.x0, b.x0);
      acc.y0 = Math.min(acc.y0, b.y0);
      acc.x1 = Math.max(acc.x1, b.x1);
      acc.y1 = Math.max(acc.y1, b.y1);
      acc.size = Math.max(acc.size, b.size);
    } else {
      byLine.set(line, { ...b });
    }
    i = j;
  }
  return [...byLine.values()].map((b) => ({
    x: b.x0 / page.widthPt,
    y: b.y0 / page.heightPt,
    w: (b.x1 - b.x0) / page.widthPt,
    h: (b.y1 - b.y0) / page.heightPt,
    lineHeightPt: b.size,
  }));
}

export interface Segment {
  start: number;
  end: number;
  /** Page points; NaN for rotated text. */
  x0: number;
  x1: number;
  y: number;
  size: number;
}

export interface StreamLine {
  start: number;
  end: number;
  segments: Segment[];
}

const linesCache = new WeakMap<TextStream, StreamLine[]>();

export function streamLines(stream: TextStream): StreamLine[] {
  const cached = linesCache.get(stream);
  if (cached) return cached;
  const { text, refs, page } = stream;
  const lines: StreamLine[] = [];
  let lineStart = 0;
  for (let i = 0; i <= text.length; i++) {
    if (i < text.length && text[i] !== "\n") continue;
    const segments: Segment[] = [];
    let segStart = lineStart;
    for (let j = lineStart; j <= i; j++) {
      if (j < i && text[j] !== "\t") continue;
      let s = segStart;
      let e = j;
      while (s < e && text[s] === " ") s++;
      while (e > s && text[e - 1] === " ") e--;
      if (e > s) {
        const first = refs.slice(s, e).find((r) => r !== null) ?? null;
        const last = [...refs.slice(s, e)].reverse().find((r) => r !== null) ?? null;
        let x0 = NaN;
        let x1 = NaN;
        let y = NaN;
        let size = 0;
        if (first && last) {
          const a = page.runs[first.run];
          const b = page.runs[last.run];
          if (Math.abs(a.dy) < 0.01 && Math.abs(b.dy) < 0.01 && a.dx > 0 && b.dx > 0) {
            x0 = runSliceBounds(a, first.index, first.index + 1).x0;
            x1 = runSliceBounds(b, last.index, last.index + 1).x1;
            y = a.oy;
            size = runSize(a);
          }
        }
        segments.push({ start: s, end: e, x0, x1, y, size });
      }
      segStart = j + 1;
    }
    lines.push({ start: lineStart, end: i, segments });
    lineStart = i + 1;
  }
  linesCache.set(stream, lines);
  return lines;
}

export function padRect(rect: Rect & { lineHeightPt: number }, page: Pick<PageText, "widthPt" | "heightPt">): Rect {
  const padX = (0.22 * rect.lineHeightPt) / page.widthPt;
  const padY = (0.14 * rect.lineHeightPt) / page.heightPt;
  const x0 = Math.max(0, rect.x - padX);
  const y0 = Math.max(0, rect.y - padY);
  const x1 = Math.min(1, rect.x + rect.w + padX);
  const y1 = Math.min(1, rect.y + rect.h + padY);
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

const ALNUM = /[\p{L}\p{N}]/u;

export function isAlnum(ch: string | undefined): boolean {
  return ch !== undefined && ALNUM.test(ch);
}

export function compactStream(text: string): { chars: string; toStream: number[] } {
  let chars = "";
  const toStream: number[] = [];
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (!ALNUM.test(ch)) continue;
    chars += foldChar(ch);
    toStream.push(i);
  }
  return { chars, toStream };
}

/** Always one character out, so offsets stay aligned. */
export function foldChar(ch: string): string {
  const base = ch.normalize("NFD")[0] ?? ch;
  return base.toLowerCase().slice(0, 1) || ch;
}

export function compactValue(value: string): string {
  return compactStream(value).chars;
}
