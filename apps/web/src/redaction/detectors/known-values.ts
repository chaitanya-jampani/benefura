import type { AliasKind } from "@/db/dexie";

import type { Detection, KnownValue, TextSource } from "../types";
import { compactStream, compactValue, type TextStream } from "./text-stream";

const HONORIFIC = /^(?:mr|mrs|ms|mx|miss|dr|prof)\.?$/i;

const STREET_ABBREVIATIONS: ReadonlyArray<[string, string]> = [
  ["street", "st"],
  ["avenue", "ave"],
  ["road", "rd"],
  ["drive", "dr"],
  ["boulevard", "blvd"],
  ["court", "ct"],
  ["crescent", "cres"],
  ["place", "pl"],
  ["lane", "ln"],
  ["terrace", "tce"],
  ["highway", "hwy"],
  ["parade", "pde"],
  ["circuit", "cct"],
  ["close", "cl"],
  ["apartment", "apt"],
  ["suite", "ste"],
  ["north", "n"],
  ["south", "s"],
  ["east", "e"],
  ["west", "w"],
];

export interface Variant {
  value: string;
  /** A name or address piece matched on its own. */
  part: boolean;
}

function swapAbbreviations(value: string): string[] {
  const words = value.split(/(\s+)/);
  const swap = (w: string, from: 0 | 1) => {
    const m = /^(.*?)([.,;]*)$/.exec(w)!;
    const hit = STREET_ABBREVIATIONS.find((pair) => pair[from] === m[1].toLowerCase());
    return hit ? hit[from === 0 ? 1 : 0] + m[2].replace(/^\./, "") : w;
  };
  const long = words.map((w) => swap(w, 1));
  const short = words.map((w) => swap(w, 0));
  return [long.join(""), short.join("")];
}

export function knownVariants(known: KnownValue): Variant[] {
  const value = known.value.trim();
  if (!value) return [];
  const out: Variant[] = [{ value, part: false }];

  if (known.isName) {
    const tokens = value
      .split(/[\s,]+/)
      .filter((t) => t && !HONORIFIC.test(t))
      .map((t) => t.replace(/[.,]+$/, ""));
    if (tokens.length >= 2) {
      const first = tokens[0];
      const last = tokens[tokens.length - 1];
      out.push({ value: `${first} ${last}`, part: false }, { value: `${last} ${first}`, part: false });
      if (tokens.length > 2) out.push({ value: `${last} ${tokens.slice(0, -1).join(" ")}`, part: false });
      for (const t of tokens) if (compactValue(t).length >= 2) out.push({ value: t, part: true });
    }
  }

  if (known.kind === "address") {
    for (const v of swapAbbreviations(value)) out.push({ value: v, part: false });
    for (const piece of value.split(/[,\n]+/)) {
      if (compactValue(piece).length >= 6) {
        out.push({ value: piece.trim(), part: true });
        for (const v of swapAbbreviations(piece.trim())) out.push({ value: v, part: true });
      }
    }
  }

  if (known.kind === "phone") {
    const d = value.replace(/\D/g, "");
    if (d.length === 11 && d.startsWith("1")) out.push({ value: d.slice(1), part: false });
    if (d.length === 10 && !d.startsWith("0")) out.push({ value: `1${d}`, part: false });
    if (d.length === 11 && d.startsWith("61")) out.push({ value: `0${d.slice(2)}`, part: false });
    if (d.length === 10 && d.startsWith("0")) out.push({ value: `61${d.slice(1)}`, part: false });
  }

  const seen = new Set<string>();
  return out.filter((v) => {
    const key = `${compactValue(v.value)}|${v.part}`;
    if (!compactValue(v.value) || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// Letters OCR confuses with digits; folded only for long numbers on OCR pages.
const OCR_DIGIT_FOLD: Record<string, string> = { o: "0", i: "1", l: "1", s: "5", b: "8", z: "2" };

function foldDigits(s: string): string {
  let out = "";
  for (const ch of s) out += OCR_DIGIT_FOLD[ch] ?? ch;
  return out;
}

const KNOWN_LABEL: Record<AliasKind, string> = {
  member: "Name you entered",
  employer: "Employer you entered",
  policy: "Number you entered",
  address: "Address you entered",
  phone: "Phone you entered",
  email: "Email you entered",
  identifier: "Number you entered",
  custom: "Value you added",
};

const isDigit = (ch: string | undefined) => ch !== undefined && ch >= "0" && ch <= "9";
const isLetter = (ch: string | undefined) => ch !== undefined && /\p{L}/u.test(ch);

function boundaryOk(text: string, start: number, end: number, first: string, last: string): boolean {
  const before = text[start - 1];
  const after = text[end];
  if (isDigit(first) ? isDigit(before) : isLetter(before)) return false;
  if (isDigit(last) ? isDigit(after) : isLetter(after)) return false;
  return true;
}

export function findKnownValues(
  pageIndex: number,
  stream: TextStream,
  known: readonly KnownValue[],
  source: TextSource = "pdf",
): Detection[] {
  const compact = compactStream(stream.text);
  const folded = source === "ocr" ? foldDigits(compact.chars) : null;
  const out: Detection[] = [];

  for (const k of known) {
    for (const variant of knownVariants(k)) {
      const needle = compactValue(variant.value);
      const numeric = (needle.match(/\d/g)?.length ?? 0) >= 6;
      const haystack = folded && numeric ? folded : compact.chars;
      const target = folded && numeric ? foldDigits(needle) : needle;
      if (target.length < 2) continue;

      const lead = /^[^\p{L}\p{N}\s]+/u.exec(variant.value.trim())?.[0] ?? "";
      const trail = /[^\p{L}\p{N}\s]+$/u.exec(variant.value.trim())?.[0] ?? "";
      for (let pos = haystack.indexOf(target); pos >= 0; pos = haystack.indexOf(target, pos + 1)) {
        let start = compact.toStream[pos];
        let end = compact.toStream[pos + target.length - 1] + 1;
        if (!boundaryOk(stream.text, start, end, needle[0], needle[needle.length - 1])) continue;
        if (lead && stream.text.slice(start - lead.length, start) === lead) start -= lead.length;
        if (trail && stream.text.slice(end, end + trail.length) === trail) end += trail.length;
        const raw = stream.text.slice(start, end);
        // Don't stitch a value together across table columns or far-apart lines.
        if (raw.includes("\t") || raw.split("\n").length > 2 || raw.length > variant.value.length * 2 + 4) continue;
        out.push({
          pageIndex,
          start,
          end,
          value: raw,
          kind: k.kind,
          family: null,
          token: k.token,
          detector: variant.part ? "known.part" : "known",
          label: variant.part ? "Part of a name or address you entered" : KNOWN_LABEL[k.kind],
          confidence: variant.part ? 0.8 : 0.99,
          signals: { known: true },
          defaultEnabled: true,
        });
      }
    }
  }
  return out;
}
