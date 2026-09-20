import type { RedactionBox } from "@/db/dexie";

import { AliasBook, mergeBoxes, newId } from "../apply";
import type { BoxCandidate, DetectOptions, Detection, PageText } from "../types";
import { detectAU } from "./au";
import { detectCA } from "./ca";
import { detectCommon, type PatternContext } from "./common";
import { findKnownValues } from "./known-values";
import { detectLabels, findLabels } from "./labels";
import { buildTextStream, padRect, rangeRects, type TextStream } from "./text-stream";

export { buildTextStream, type TextStream } from "./text-stream";

const MONTH = String.raw`(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?`;
const FULL_DATE = new RegExp(
  String.raw`(?<![\p{L}\p{N}/.-])(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2}\s+${MONTH}\s+\d{4}|${MONTH}\s+\d{1,2},\s+\d{4})(?![\p{L}\p{N}/-])`,
  "giu",
);
// Dates with one of these just before them are about a service or the policy, not a birthday.
const NOT_A_BIRTHDAY = /(?:service|visit|invoice|issued?|paid|payment|claim|effective|start|end|from|to|until|received|appointment|treatment|due|expir\w*|renew\w*|since)[\s:#-]*$/iu;

/** Backstop for unlabelled dates of birth: a full date on the same line as a member's name. */
function datesBesideMembers(pageIndex: number, stream: TextStream, known: readonly Detection[]): Detection[] {
  const text = stream.text;
  const out: Detection[] = [];
  const lineStarts = new Set<number>();
  for (const k of known) {
    if (k.kind === "member") lineStarts.add(text.lastIndexOf("\n", k.start - 1) + 1);
  }
  for (const start of lineStarts) {
    const nl = text.indexOf("\n", start);
    const end = nl < 0 ? text.length : nl;
    const line = text.slice(start, end);
    for (const m of line.matchAll(FULL_DATE)) {
      const at = m.index ?? 0;
      if (NOT_A_BIRTHDAY.test(line.slice(Math.max(0, at - 24), at))) continue;
      out.push({
        pageIndex,
        start: start + at,
        end: start + at + m[0].length,
        value: m[0],
        kind: "identifier",
        family: null,
        detector: "dob.beside-name",
        label: "Date beside a name (likely a date of birth)",
        confidence: 0.7,
        signals: {},
        defaultEnabled: true,
      });
    }
  }
  return out;
}

export function detectInStream(pageIndex: number, stream: TextStream, opts: DetectOptions, source = stream.page.source): Detection[] {
  const labels = findLabels(stream);
  const ctx: PatternContext = { pageIndex, stream, labels };
  const known = findKnownValues(pageIndex, stream, opts.known, source);
  return [
    ...known,
    ...datesBesideMembers(pageIndex, stream, known),
    ...detectCommon(ctx),
    ...(opts.region === "CA" ? detectCA(ctx) : detectAU(ctx)),
    ...detectLabels(pageIndex, stream, labels),
  ];
}

function priority(d: Detection): number {
  if (d.signals.known) return d.detector === "known" ? 4 : 3;
  if (d.signals.checksum) return 2;
  if (d.signals.label) return 1;
  return 0;
}

function better(a: Detection, b: Detection): Detection {
  return priority(b) > priority(a) || (priority(b) === priority(a) && b.confidence > a.confidence) ? b : a;
}

export interface Cluster {
  start: number;
  end: number;
  best: Detection;
  members: Detection[];
}

export function clusterDetections(detections: readonly Detection[]): Cluster[] {
  const sorted = [...detections].sort((a, b) => a.start - b.start || b.end - a.end);
  const clusters: Cluster[] = [];
  for (const d of sorted) {
    const last = clusters.at(-1);
    if (last && d.start < last.end) {
      last.end = Math.max(last.end, d.end);
      last.members.push(d);
    } else {
      clusters.push({ start: d.start, end: d.end, best: d, members: [d] });
    }
  }
  for (const cluster of clusters) {
    // A name part inside something larger ("jordan.testcase@example.com") must not make it a name.
    const span = cluster.end - cluster.start;
    const emails = cluster.members.filter((m) => m.kind === "email");
    const insideEmail = (m: Detection) =>
      m.kind !== "email" && emails.some((e) => e.start <= m.start && e.end >= m.end && e.end - e.start > m.end - m.start);
    const eligible = cluster.members.filter(
      (m) => !insideEmail(m) && (m.detector !== "known.part" || m.end - m.start >= 0.6 * span),
    );
    cluster.best = (eligible.length ? eligible : cluster.members).reduce(better);
  }
  return clusters;
}

export function candidatesForPage(
  page: PageText,
  opts: DetectOptions,
  book: AliasBook,
  stream: TextStream = buildTextStream(page),
): BoxCandidate[] {
  const clusters = clusterDetections(detectInStream(page.pageIndex, stream, opts, page.source));
  const out: BoxCandidate[] = [];
  for (const cluster of clusters) {
    const best = cluster.best;
    const value = stream.text.slice(cluster.start, cluster.end).replace(/\s+/g, " ").trim();
    const token = best.token ?? (best.family ? book.tokenFor(best.family, best.value, best.kind) : null);
    const enabled = cluster.members.some((m) => m.signals.known) || best.defaultEnabled;
    const confidence = Math.max(...cluster.members.map((m) => m.confidence));
    for (const rect of rangeRects(stream, cluster.start, cluster.end)) {
      const padded = padRect(rect, page);
      if (padded.w <= 0 || padded.h <= 0) continue;
      const box: RedactionBox = {
        id: newId(),
        pageIndex: page.pageIndex,
        ...padded,
        token,
        kind: best.kind,
        source: "detector",
        detector: best.detector,
        confidence,
        enabled,
      };
      out.push({ box, value, label: best.label });
    }
  }
  return mergeCandidates(out);
}

/** Two passes: values found on any page in pass one become known values, so pass two finds them unlabelled elsewhere. */
export function detectDocument(pages: readonly PageText[], opts: DetectOptions, book: AliasBook): BoxCandidate[] {
  const streams = pages.map((p) => buildTextStream(p));
  pages.forEach((page, i) => candidatesForPage(page, opts, book, streams[i]));
  const typed = new Set(opts.known.map((k) => `${k.token}|${k.value}`));
  const learned = book.knownValues().filter((k) => !typed.has(`${k.token}|${k.value}`));
  const known = [...opts.known, ...learned.map((k) => ({ ...k, isName: opts.known.some((t) => t.token === k.token && t.isName) }))];
  return pages.flatMap((page, i) => candidatesForPage(page, { ...opts, known }, book, streams[i]));
}

export function mergeCandidates(candidates: readonly BoxCandidate[]): BoxCandidate[] {
  const byId = new Map(candidates.map((c) => [c.box.id, c]));
  const merged = mergeBoxes(candidates.map((c) => c.box));
  return merged.map((box) => {
    const source = byId.get(box.id)!;
    const joined = candidates
      .filter((c) => c.box.pageIndex === box.pageIndex && c.box.token === box.token && c.box.id !== box.id)
      .filter((c) => c.box.x >= box.x - 1e-6 && c.box.x + c.box.w <= box.x + box.w + 1e-6 && c.box.y >= box.y - 1e-6 && c.box.y + c.box.h <= box.y + box.h + 1e-6)
      .map((c) => c.value);
    const value = [source.value, ...joined.filter((v) => !source.value.includes(v))].join(" ");
    return { box, value, label: source.label };
  });
}

export function findValueOnPage(
  page: PageText,
  value: string,
  token: string | null,
  kind: RedactionBox["kind"],
  stream: TextStream = buildTextStream(page),
): BoxCandidate[] {
  const hits = findKnownValues(page.pageIndex, stream, [{ token: token ?? "", kind, value, isName: kind === "member" }], page.source);
  const out: BoxCandidate[] = [];
  for (const cluster of clusterDetections(hits)) {
    for (const rect of rangeRects(stream, cluster.start, cluster.end)) {
      out.push({
        box: {
          id: newId(),
          pageIndex: page.pageIndex,
          ...padRect(rect, page),
          token,
          kind,
          source: "manual",
          detector: "find-all",
          confidence: cluster.best.confidence,
          enabled: true,
        },
        value: stream.text.slice(cluster.start, cluster.end),
        label: "Value you added",
      });
    }
  }
  return out;
}
