// Given only what a user would type (names, employer), every value in samples/fixtures/<doc>.pii.json must be boxed.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { AliasBook } from "./apply";
import { candidatesForPage } from "./detectors";
import { buildTextStream, rangeRects } from "./detectors/text-stream";
import { applyFormToBook, EMPTY_FORM, type HideFormValues } from "./known";
import { closePdf, extractPageText, openPdf } from "./pdf-text";
import { PDFJS_NODE_ASSETS } from "./test-utils/node-canvas";
import type { BoxCandidate, PageText } from "./types";

interface PiiEntry {
  value: string;
  kind: string;
  page: number;
  aliasToken: string | null;
}

const SAMPLES = path.join(process.cwd(), "../../samples");

const DOCS = [
  { pdf: "ca-northwind-booklet.pdf", pii: "ca-northwind.pii.json", region: "CA" as const, notHidden: [{ page: 2, text: "Relationship" }, { page: 2, text: "Claims for your spouse" }, { page: 5, text: "Massage therapy" }] },
  { pdf: "au-wattle-policy.pdf", pii: "au-wattle.pii.json", region: "AU" as const, notHidden: [{ page: 2, text: "Role" }, { page: 12, text: "Eucalypt Mutual Bank" }, { page: 8, text: "Remedial massage" }] },
];

function formFor(entries: readonly PiiEntry[]): HideFormValues {
  const unique = (kind: string) => [...new Set(entries.filter((e) => e.kind === kind).map((e) => e.value))];
  const [self = "", ...family] = unique("person_name");
  return {
    ...EMPTY_FORM,
    selfName: self,
    employer: unique("employer_name")[0] ?? "",
    family: family.map((name, i) => ({ id: `f${i}`, name, relationship: i === 0 ? "spouse" : "child" })),
  };
}

function occurrences(text: PageText, value: string) {
  const stream = buildTextStream(text);
  const pattern = new RegExp(value.split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[ \\t\\n]+"), "g");
  return [...stream.text.matchAll(pattern)].map((m) => rangeRects(stream, m.index!, m.index! + m[0].length));
}

function covered(point: { x: number; y: number }, boxes: readonly BoxCandidate[]): boolean {
  return boxes.some(({ box }) => box.enabled && point.x >= box.x && point.x <= box.x + box.w && point.y >= box.y && point.y <= box.y + box.h);
}

describe.each(DOCS)("$pdf", ({ pdf: file, pii, region, notHidden }) => {
  const available = existsSync(path.join(SAMPLES, file));
  const texts = new Map<number, PageText>();
  const candidates = new Map<number, BoxCandidate[]>();
  let entries: PiiEntry[] = [];

  beforeAll(async () => {
    if (!available) return;
    entries = JSON.parse(readFileSync(path.join(SAMPLES, "fixtures", pii), "utf8"));
    const doc = await openPdf(new Uint8Array(readFileSync(path.join(SAMPLES, file))), { assetBase: PDFJS_NODE_ASSETS });
    const book = new AliasBook();
    const { known } = applyFormToBook(formFor(entries), book);
    for (let p = 1; p <= doc.numPages; p++) {
      const text = await extractPageText(await doc.getPage(p), p - 1);
      texts.set(p, text);
      candidates.set(p, candidatesForPage(text, { region, known }, book));
    }
    await closePdf(doc);
  });

  afterAll(() => {
    texts.clear();
    candidates.clear();
  });

  it.skipIf(!available)("hides every listed PII occurrence", () => {
    const misses: string[] = [];
    for (const entry of entries) {
      const found = occurrences(texts.get(entry.page)!, entry.value);
      expect(found.length, `${entry.kind} "${entry.value}" should be on page ${entry.page}`).toBeGreaterThan(0);
      for (const rects of found) {
        for (const r of rects) {
          // Sample across the glyphs: every point must sit inside an enabled box.
          for (const fx of [0.02, 0.25, 0.5, 0.75, 0.98]) {
            for (const fy of [0.3, 0.7]) {
              const point = { x: r.x + r.w * fx, y: r.y + r.h * fy };
              if (!covered(point, candidates.get(entry.page)!)) {
                misses.push(`page ${entry.page} ${entry.kind} "${entry.value}" at ${fx},${fy}`);
              }
            }
          }
        }
      }
    }
    expect(misses).toEqual([]);
  });

  it.skipIf(!available)("gives values their expected alias family", () => {
    const tokens = new Map<string, Set<string>>();
    for (const [, cs] of candidates) {
      for (const c of cs) if (c.box.enabled && c.box.token) tokens.set(c.box.token, (tokens.get(c.box.token) ?? new Set()).add(c.value));
    }
    for (const entry of entries.filter((e) => e.aliasToken)) {
      const family = entry.aliasToken!.match(/^\[([A-Z]+)_/)![1];
      const hit = [...tokens.entries()].some(([token, values]) => token.startsWith(`[${family}_`) && [...values].some((v) => v.replace(/\s+/g, " ").includes(entry.value.replace(/\.$/, ""))));
      expect(hit, `${entry.kind} "${entry.value}" should be labelled [${family}_…]`).toBe(true);
    }
  });

  it.skipIf(!available)("leaves plan content visible", () => {
    for (const { page, text } of notHidden) {
      const found = occurrences(texts.get(page)!, text);
      expect(found.length, `"${text}" should be on page ${page}`).toBeGreaterThan(0);
      for (const r of found.flat()) {
        expect(covered({ x: r.x + r.w / 2, y: r.y + r.h / 2 }, candidates.get(page)!), `"${text}" on page ${page} is hidden`).toBe(false);
      }
    }
  });
});
