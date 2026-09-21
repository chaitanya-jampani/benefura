import type { AliasKind } from "@/db/dexie";

import type { Detection, TokenFamily } from "../types";
import { scoreSignals } from "./score";
import { streamLines, type Segment, type StreamLine, type TextStream } from "./text-stream";

type Shape = "name" | "id" | "date" | "org" | "line" | "phone" | "email";

export interface LabelDef {
  id: string;
  pattern: RegExp;
  shape: Shape;
  kind: AliasKind;
  family: TokenFamily | null;
  label: string;
  /** Generic words ("Name", "Address") only count at a line or column start with a colon or column gap after. */
  strict?: boolean;
}

const NO = String.raw`(?:no\.?|number|num\.?|#|nbr\.?)`;

export const LABEL_DEFS: readonly LabelDef[] = [
  {
    id: "member_name",
    pattern: new RegExp(
      String.raw`(?:plan\s+|primary\s+|principal\s+)?member(?:'s|’s)?\s+name|name\s+of\s+(?:the\s+)?(?:plan\s+)?member|employee(?:'s|’s)?\s+name|insured(?:\s+person)?(?:'s|’s)?\s+name|patient(?:'s|’s)?\s+name|policy\s*holder(?:'s|’s)?(?:\s+name)?|card\s*holder(?:'s|’s)?(?:\s+name)?|principal\s+member|primary\s+member|plan\s+member|full\s+name|claimant(?:'s|’s)?\s+name`,
      "iu",
    ),
    shape: "name",
    kind: "member",
    family: "MEMBER",
    label: "Name after a label",
  },
  {
    id: "dependant_name",
    pattern: /(?:spouse|partner|dependa?e?nt|child)(?:'s|’s)?\s+name/iu,
    shape: "name",
    kind: "member",
    family: "MEMBER",
    label: "Family member name after a label",
  },
  {
    id: "name_generic",
    pattern: /name|member|insured|patient|claimant|surname|(?:first|last|given|family)\s+names?|spouse|partner|dependa?e?nt|child/iu,
    shape: "name",
    kind: "member",
    family: "MEMBER",
    label: "Name after a label",
    strict: true,
  },
  {
    id: "certificate",
    pattern: new RegExp(
      String.raw`certificate\s*(?:${NO}|id)?|cert\.?\s*${NO}|member\s*(?:id|${NO})|employee\s*(?:id|${NO})|id\s*${NO}|identification\s+${NO}|card\s*${NO}`,
      "iu",
    ),
    shape: "id",
    kind: "policy",
    family: "CERT",
    label: "Certificate or member number",
  },
  {
    id: "policy",
    // Longest alternative first so "Group policy number" isn't cut short; footers ("Group policy G-40718-2")
    // lack "number", and the id shape keeps "group policy issued in Ontario" out.
    pattern: new RegExp(
      String.raw`(?:group\s+)?(?:policy|plan|contract)\s*(?:${NO}|id)|(?:group|division|membership)\s*(?:${NO}|id)|group\s+(?:policy|plan|contract)`,
      "iu",
    ),
    shape: "id",
    kind: "policy",
    family: "POLICY",
    label: "Policy or group number",
  },
  {
    id: "reference",
    pattern: new RegExp(String.raw`(?:client|customer|reference|claim|account\s+holder)\s*(?:${NO}|id)|ref\.?\s*${NO}`, "iu"),
    shape: "id",
    kind: "identifier",
    family: "ID",
    label: "Reference number",
  },
  {
    id: "dob",
    pattern: /date\s+of\s+birth|birth\s*date|d\.?\s?o\.?\s?b\.?|born/iu,
    shape: "date",
    kind: "identifier",
    family: null,
    label: "Date of birth",
  },
  {
    id: "employer",
    pattern: /employer(?:'s|’s)?(?:\s+name)?|plan\s+sponsor|company\s+name|organi[sz]ation\s+name/iu,
    shape: "org",
    kind: "employer",
    family: "EMPLOYER",
    label: "Employer after a label",
  },
  {
    id: "address",
    pattern: /(?:home|mailing|residential|street|postal|member|patient)\s+address|address/iu,
    shape: "line",
    kind: "address",
    family: "ADDRESS",
    label: "Address after a label",
    strict: true,
  },
  {
    id: "phone",
    pattern: new RegExp(String.raw`(?:home\s+|work\s+|daytime\s+)?(?:tel(?:ephone)?|phone|mobile|cell)(?:\s*${NO})?`, "iu"),
    shape: "phone",
    kind: "phone",
    family: "PHONE",
    label: "Phone number after a label",
    strict: true,
  },
  {
    id: "email",
    pattern: /e-?mail(?:\s+address)?/iu,
    shape: "email",
    kind: "email",
    family: "EMAIL",
    label: "Email after a label",
    strict: true,
  },
  {
    id: "sin",
    pattern: new RegExp(String.raw`social\s+insurance\s*(?:${NO})?`, "iu"),
    shape: "id",
    kind: "identifier",
    family: "ID",
    label: "Social insurance number",
  },
  {
    id: "sin_acronym",
    pattern: /S\.?I\.?N\.?/u,
    shape: "id",
    kind: "identifier",
    family: "ID",
    label: "Social insurance number",
  },
  {
    id: "health",
    pattern: new RegExp(
      String.raw`(?:personal\s+)?health\s+(?:card|insurance|services?|care)?\s*${NO}|health\s+number|medicare\s*(?:card\s*)?(?:${NO})?|individual\s+healthcare\s+identifier|tax\s+file\s*(?:${NO})?`,
      "iu",
    ),
    shape: "id",
    kind: "identifier",
    family: "ID",
    label: "Health or tax number",
  },
  {
    id: "health_acronym",
    pattern: new RegExp(String.raw`(?:OHIP|PHN|IHI|RAMQ|TFN|HCN)(?:\s*${NO})?`, "u"),
    shape: "id",
    kind: "identifier",
    family: "ID",
    label: "Health or tax number",
  },
  {
    id: "bank",
    pattern: new RegExp(String.raw`(?:bank\s+)?account\s*${NO}|BSB(?:\s*${NO})?|transit\s*${NO}|institution\s*${NO}`, "iu"),
    shape: "id",
    kind: "identifier",
    family: null,
    label: "Bank details",
  },
];

export interface LabelHit {
  def: LabelDef;
  start: number;
  end: number;
}

/** Keeps the longest when labels overlap ("Member name" over "name"). */
export function findLabels(stream: TextStream): LabelHit[] {
  const { text } = stream;
  const hits: LabelHit[] = [];
  for (const def of LABEL_DEFS) {
    const flags = new Set([...def.pattern.flags, "g", "u"]);
    const re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${def.pattern.source})(?![\\p{L}\\p{N}])`, [...flags].join(""));
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      if (!m[0]) {
        re.lastIndex++;
        continue;
      }
      const start = m.index;
      const end = start + m[0].length;
      if (def.strict) {
        const lineStart = Math.max(text.lastIndexOf("\n", start - 1), text.lastIndexOf("\t", start - 1)) + 1;
        if (text.slice(lineStart, start).trim().length > 0) continue;
        if (!/^\s*(?::|\t)/.test(text.slice(end, end + 4))) continue;
      }
      hits.push({ def, start, end });
    }
  }
  hits.sort((a, b) => a.start - b.start || b.end - b.start - (a.end - a.start));
  const kept: LabelHit[] = [];
  for (const hit of hits) {
    const last = kept.at(-1);
    if (last && hit.start < last.end) continue;
    kept.push(hit);
  }
  return kept;
}

const CONNECTOR = /^[ \t:#.\-–—]*(?:\((?:if\s+any|optional)\))?[ \t:#.\-–—]*$/iu;

export function labelBefore(labels: LabelHit[], text: string, start: number, ids?: readonly string[]): boolean {
  for (let i = labels.length - 1; i >= 0; i--) {
    const hit = labels[i];
    if (hit.end > start) continue;
    if (start - hit.end > 24) return false;
    if (ids && !ids.includes(hit.def.id)) continue;
    if (CONNECTOR.test(text.slice(hit.end, start))) return true;
  }
  return false;
}

const MONTH = String.raw`(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?`;
const SHAPES: Record<Shape, RegExp> = {
  name: /^(?:(?:[Mm]rs?|[Mm][sx]|[Mm]iss|[Dd]r)\.?\s+)?\p{Lu}[\p{L}'’.-]*(?:,?\s{1,2}\p{Lu}[\p{L}'’.-]*){0,4}/u,
  id: /^[A-Z]{0,4}[ -]?\d[0-9A-Z]*(?:[ \-/.]?[0-9A-Z]*\d[0-9A-Z]*)*(?:[ -][A-Z]{1,2}(?![\p{L}\p{N}]))?/u,
  date: new RegExp(
    String.raw`^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/. ]\d{1,2}[-/. ]\d{2,4}|\d{1,2}\s+${MONTH},?\s+\d{4}|${MONTH}\s+\d{1,2},?\s+\d{4})`,
    "iu",
  ),
  org: /^[\p{Lu}\p{N}][^\n\t]{1,79}/u,
  line: /^[\p{L}\p{N}#][^\n\t]{3,119}/u,
  phone: /^\+?\(?\d[\d ()\-.]{5,}\d/u,
  email: /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}/u,
};

// Words that start another field, so a capture stops there; one that starts with them is a column header.
const FIELD_WORDS = new Set(
  "policy group certificate cert plan member members date birth employee employer id number no address phone telephone email effective coverage class division relationship role details type spouse partner dependant dependent child status province state postcode postal signature sin medicare health account bsb page section benefit benefits name".split(
    " ",
  ),
);
const NAME_CONNECTORS = new Set(["and", "or", "the", "of"]);

function trimAtStopWord(value: string, shape: Shape): string {
  const parts = value.split(/(\s+|,\s*)/);
  let out = "";
  for (const part of parts) {
    const word = part.trim().replace(/[.,]$/, "").toLowerCase();
    if (word && FIELD_WORDS.has(word)) break;
    if (word && out && shape === "name" && NAME_CONNECTORS.has(word)) break;
    out += part;
  }
  return out;
}

const TRAILING = /[\s,;:.]+$/u;

interface Span {
  start: number;
  end: number;
}

function matchShape(text: string, offset: number, segmentEnd: number, shape: Shape): Span | null {
  const segment = text.slice(offset, segmentEnd);
  const m = SHAPES[shape].exec(segment);
  if (!m) return null;
  let value = m[0];
  if (shape === "name" || shape === "org") value = trimAtStopWord(value, shape);
  value = value.replace(TRAILING, "");
  if (shape === "id" && (value.match(/\d/g)?.length ?? 0) < 3) return null;
  if (shape === "name" && !/\p{L}{2}/u.test(value)) return null;
  if (shape === "line" && !/\d/.test(value) && value.length < 8) return null;
  if (!value) return null;
  return { start: offset, end: offset + value.length };
}

/** Same line (across a column gap), or the next line if the label ends its line. */
function captureValue(text: string, from: number, shape: Shape): Span | null {
  let i = from;
  // "." isn't skipped: "…their own employer. Claims for your spouse" is prose, not a field.
  while (i < text.length && /[ \t:#\-–—]/.test(text[i])) i++;
  if (text[i] === "\n" || i >= text.length) {
    if (text[i] !== "\n") return null;
    i++;
    while (i < text.length && text[i] === " ") i++;
  }
  const n = text.indexOf("\n", i);
  const t = text.indexOf("\t", i);
  const ends = [n, t].filter((x) => x >= 0);
  return matchShape(text, i, ends.length ? Math.min(...ends) : text.length, shape);
}

function lineIndexAt(lines: readonly StreamLine[], index: number): number {
  return lines.findIndex((l) => index >= l.start && index <= l.end);
}

function segmentAt(line: StreamLine | undefined, index: number): Segment | undefined {
  return line?.segments.find((s) => index >= s.start && index < s.end);
}

const overlaps = (a0: number, a1: number, b0: number, b1: number) => a0 <= b1 && b0 <= a1;

/** Wrapped address: following lines whose first cell starts under the value, not in the label column. */
function continuationSpans(stream: TextStream, lines: readonly StreamLine[], hit: LabelHit, value: Span): Span[] {
  const li = lineIndexAt(lines, value.start);
  const valueSeg = segmentAt(lines[li], value.start);
  const labelSeg = segmentAt(lines[lineIndexAt(lines, hit.start)], hit.start);
  if (!valueSeg || !labelSeg || Number.isNaN(valueSeg.x0)) return [];
  const out: Span[] = [];
  let prev = valueSeg;
  for (let i = li + 1; i < lines.length && out.length < 2; i++) {
    const first = lines[i].segments[0];
    if (!first || Number.isNaN(first.x0)) break;
    if (Math.abs(first.x0 - valueSeg.x0) > 3 || first.x0 <= labelSeg.x0 + 5) break;
    if (first.y - prev.y > 1.8 * Math.max(prev.size, first.size)) break;
    const span = matchShape(stream.text, first.start, first.end, "line") ?? { start: first.start, end: first.end };
    out.push(span);
    prev = first;
  }
  return out;
}

/** Header above a column ("Date of birth"): cells below it until one is missing or misshapen. */
function columnSpans(stream: TextStream, lines: readonly StreamLine[], hit: LabelHit): Span[] {
  const li = lineIndexAt(lines, hit.start);
  const header = lines[li];
  const headerSeg = segmentAt(header, hit.start);
  if (!header || !headerSeg || header.segments.length < 2 || Number.isNaN(headerSeg.x0)) return [];
  const out: Span[] = [];
  let prevY = headerSeg.y;
  for (let i = li + 1; i < lines.length && i <= li + 40; i++) {
    const cell = lines[i].segments.find((s) => !Number.isNaN(s.x0) && overlaps(s.x0, s.x1, headerSeg.x0 - 4, headerSeg.x1 + 4));
    if (!cell || cell.y - prevY > 3 * Math.max(cell.size, headerSeg.size)) break;
    const span = matchShape(stream.text, cell.start, cell.end, hit.def.shape);
    if (!span) break;
    out.push(span);
    prevY = cell.y;
  }
  return out;
}

const SHAPE_BASE: Record<Shape, number> = {
  name: 0.5,
  id: 0.52,
  date: 0.6,
  org: 0.45,
  line: 0.45,
  phone: 0.55,
  email: 0.6,
};

export function detectLabels(pageIndex: number, stream: TextStream, labels: LabelHit[]): Detection[] {
  const out: Detection[] = [];
  const lines = streamLines(stream);
  const push = (hit: LabelHit, spans: Span[], detector: string) => {
    // Pieces of a wrapped address share the joined value so they get one token.
    const value = spans.map((s) => stream.text.slice(s.start, s.end)).join(", ");
    for (const span of spans) {
      out.push({
        pageIndex,
        start: span.start,
        end: span.end,
        value,
        kind: hit.def.kind,
        family: hit.def.family,
        detector,
        label: hit.def.label,
        confidence: scoreSignals({ label: true }, SHAPE_BASE[hit.def.shape]),
        signals: { label: true },
        defaultEnabled: true,
      });
    }
  };
  for (const hit of labels) {
    const span = captureValue(stream.text, hit.end, hit.def.shape);
    if (span) {
      const spans = hit.def.shape === "line" ? [span, ...continuationSpans(stream, lines, hit, span)] : [span];
      push(hit, spans, `label.${hit.def.id}`);
      continue;
    }
    if (hit.def.shape === "date" || hit.def.shape === "id") {
      for (const cell of columnSpans(stream, lines, hit)) push(hit, [cell], `label.${hit.def.id}.column`);
    }
  }
  return out;
}
