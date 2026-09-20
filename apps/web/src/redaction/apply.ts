// Raw values stay in the local `aliases` table and are never sent; only tokens leave the device.
import type { AliasKind, AliasRecord, RedactionBox } from "@/db/dexie";

import { compactValue } from "./detectors/text-stream";
import type { KnownValue, TokenFamily } from "./types";

export const FAMILY_KIND: Record<TokenFamily, AliasKind> = {
  MEMBER: "member",
  EMPLOYER: "employer",
  POLICY: "policy",
  CERT: "policy",
  ADDRESS: "address",
  PHONE: "phone",
  EMAIL: "email",
  ID: "identifier",
};

export const KIND_FAMILY: Record<AliasKind, TokenFamily> = {
  member: "MEMBER",
  employer: "EMPLOYER",
  policy: "POLICY",
  address: "ADDRESS",
  phone: "PHONE",
  email: "EMAIL",
  identifier: "ID",
  custom: "ID",
};

const LETTER_FAMILIES: ReadonlySet<TokenFamily> = new Set(["MEMBER", "EMPLOYER", "ADDRESS", "PHONE", "EMAIL"]);
const FAMILIES = Object.keys(FAMILY_KIND) as TokenFamily[];

function letters(n: number): string {
  let s = "";
  let i = n;
  do {
    s = String.fromCharCode(65 + (i % 26)) + s;
    i = Math.floor(i / 26) - 1;
  } while (i >= 0);
  return s;
}

function lettersToIndex(s: string): number {
  let n = 0;
  for (const ch of s) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}

/** 0 → "[MEMBER_A]", 26 → "[MEMBER_AA]"; numbered families start at 1: 0 → "[POLICY_1]". */
export function formatToken(family: TokenFamily, index: number): string {
  return `[${family}_${LETTER_FAMILIES.has(family) ? letters(index) : index + 1}]`;
}

export function parseToken(token: string): { family: TokenFamily; index: number } | null {
  const m = /^\[([A-Z]+)_([A-Z]+|\d+)\]$/.exec(token.trim());
  if (!m) return null;
  const family = m[1] as TokenFamily;
  if (!FAMILIES.includes(family)) return null;
  if (LETTER_FAMILIES.has(family)) return /^[A-Z]+$/.test(m[2]) ? { family, index: lettersToIndex(m[2]) } : null;
  return /^\d+$/.test(m[2]) && Number(m[2]) >= 1 ? { family, index: Number(m[2]) - 1 } : null;
}

export function nextFreeToken(family: TokenFamily, used: Iterable<string>): string {
  const taken = new Set<number>();
  for (const token of used) {
    const parsed = parseToken(token);
    if (parsed?.family === family) taken.add(parsed.index);
  }
  let i = 0;
  while (taken.has(i)) i++;
  return formatToken(family, i);
}

export function compareTokens(a: string, b: string): number {
  const pa = parseToken(a);
  const pb = parseToken(b);
  if (!pa || !pb) return a.localeCompare(b);
  const order = FAMILIES.indexOf(pa.family) - FAMILIES.indexOf(pb.family);
  return order || pa.index - pb.index;
}

/** "member a" or "MEMBER_A" → "[MEMBER_A]"; null if invalid. */
export function normalizeToken(input: string): string | null {
  const cleaned = input.trim().toUpperCase().replace(/^\[|\]$/g, "").replace(/[\s-]+/g, "_");
  const token = `[${cleaned}]`;
  return /^\[[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\]$/.test(token) && token.length <= 24 ? token : null;
}

export function normalizeAliasValue(value: string): string {
  return compactValue(value);
}

function recordId(token: string): string {
  return `alias:${token.replace(/[[\]]/g, "").toLowerCase()}`;
}

export class AliasBook {
  private readonly records = new Map<string, AliasRecord>();
  private readonly byValue = new Map<string, string>();
  private readonly dirty = new Set<string>();

  constructor(
    existing: readonly AliasRecord[] = [],
    private readonly now: () => string = () => new Date().toISOString(),
  ) {
    for (const r of existing) {
      this.records.set(r.token, { ...r, values: [...r.values] });
      for (const v of r.values) {
        const key = normalizeAliasValue(v);
        if (key) this.byValue.set(key, r.token);
      }
    }
  }

  clone(): AliasBook {
    const copy = new AliasBook([...this.records.values()], this.now);
    for (const t of this.dirty) copy.dirty.add(t);
    return copy;
  }

  lookup(value: string): string | undefined {
    return this.byValue.get(normalizeAliasValue(value));
  }

  get(token: string): AliasRecord | undefined {
    return this.records.get(token);
  }

  nextToken(family: TokenFamily): string {
    const used = new Set<number>();
    for (const token of this.records.keys()) {
      const parsed = parseToken(token);
      if (parsed?.family === family) used.add(parsed.index);
    }
    let i = 0;
    while (used.has(i)) i++;
    return formatToken(family, i);
  }

  tokenFor(family: TokenFamily, value: string, kind: AliasKind = FAMILY_KIND[family]): string {
    const existing = this.lookup(value);
    if (existing) return existing;
    const token = this.nextToken(family);
    this.assign(token, kind, value);
    return token;
  }

  /** Moves `value` off any other token. */
  assign(token: string, kind: AliasKind, value: string, relationship?: AliasRecord["relationship"]): void {
    const key = normalizeAliasValue(value);
    if (!key) return;
    const previous = this.byValue.get(key);
    if (previous && previous !== token) {
      const prev = this.records.get(previous);
      if (prev) {
        prev.values = prev.values.filter((v) => normalizeAliasValue(v) !== key);
        this.dirty.add(previous);
      }
    }
    let record = this.records.get(token);
    if (!record) {
      record = { id: recordId(token), kind, token, values: [], createdAt: this.now() };
      this.records.set(token, record);
      this.dirty.add(token);
    }
    if (relationship && record.relationship !== relationship) {
      record.relationship = relationship;
      this.dirty.add(token);
    }
    if (!record.values.some((v) => normalizeAliasValue(v) === key)) {
      record.values.push(value.trim());
      this.dirty.add(token);
    }
    this.byValue.set(key, token);
  }

  /** Records left with no values are removed. */
  changed(): { put: AliasRecord[]; remove: string[] } {
    const put: AliasRecord[] = [];
    const remove: string[] = [];
    for (const token of this.dirty) {
      const r = this.records.get(token);
      if (!r) continue;
      if (r.values.length === 0) remove.push(r.id);
      else put.push({ ...r, values: [...r.values] });
    }
    return { put, remove };
  }

  all(): AliasRecord[] {
    return [...this.records.values()].filter((r) => r.values.length > 0);
  }

  knownValues(): KnownValue[] {
    return this.all().flatMap((r) =>
      r.values.map((value) => ({ token: r.token, kind: r.kind, value, isName: r.kind === "member" })),
    );
  }
}

function area(b: Pick<RedactionBox, "w" | "h">): number {
  return Math.max(0, b.w) * Math.max(0, b.h);
}

function intersection(a: RedactionBox, b: RedactionBox): number {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

function union(a: RedactionBox, b: RedactionBox): RedactionBox {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  const best = b.confidence > a.confidence ? b : a;
  return {
    ...best,
    x,
    y,
    w: Math.max(a.x + a.w, b.x + b.w) - x,
    h: Math.max(a.y + a.h, b.y + b.h) - y,
    confidence: Math.max(a.confidence, b.confidence),
    enabled: a.enabled || b.enabled,
  };
}

function sameLineNeighbours(a: RedactionBox, b: RedactionBox): boolean {
  const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  if (overlapY < 0.6 * Math.min(a.h, b.h)) return false;
  const gap = Math.max(a.x, b.x) - Math.min(a.x + a.w, b.x + b.w);
  // `h` is a fraction of page height and `x` of page width; for portrait pages this is conservative.
  return gap < 0.6 * Math.max(a.h, b.h);
}

/** Merges overlapping same-token boxes and same-token neighbours on a line; manual boxes never merge. */
export function mergeBoxes(boxes: readonly RedactionBox[]): RedactionBox[] {
  const pending = boxes.map((b) => ({ ...b }));
  let changed = true;
  while (changed) {
    changed = false;
    outer: for (let i = 0; i < pending.length; i++) {
      for (let j = i + 1; j < pending.length; j++) {
        const a = pending[i];
        const b = pending[j];
        if (a.pageIndex !== b.pageIndex || a.token !== b.token || a.enabled !== b.enabled) continue;
        if (a.source === "manual" || b.source === "manual") continue;
        const overlap = intersection(a, b) / Math.max(1e-9, Math.min(area(a), area(b)));
        if (overlap >= 0.5 || (a.token !== null && sameLineNeighbours(a, b))) {
          pending[i] = union(a, b);
          pending.splice(j, 1);
          changed = true;
          break outer;
        }
      }
    }
  }
  return pending;
}

/**
 * CU word polygons are in inches for PDF pages but pixels for plain images, so a coordinate well past
 * the page size in inches (with `pixelSize` given) means pixels.
 */
export function rectFromPolygon(
  polygon: readonly number[] | null | undefined,
  pageInches: { width: number; height: number },
  pixelSize?: { width: number; height: number },
): { x: number; y: number; w: number; h: number } | null {
  if (!polygon || polygon.length < 4 || polygon.length % 2 !== 0) return null;
  const xs = polygon.filter((_, i) => i % 2 === 0);
  const ys = polygon.filter((_, i) => i % 2 === 1);
  if ([...xs, ...ys].some((v) => !Number.isFinite(v))) return null;
  const looksLikePixels =
    pixelSize !== undefined && (Math.max(...xs) > pageInches.width * 1.1 || Math.max(...ys) > pageInches.height * 1.1);
  const width = looksLikePixels ? pixelSize.width : pageInches.width;
  const height = looksLikePixels ? pixelSize.height : pageInches.height;
  const padX = looksLikePixels ? (0.05 / pageInches.width) * width : 0.05;
  const padY = looksLikePixels ? (0.05 / pageInches.height) * height : 0.05;
  const x0 = Math.max(0, (Math.min(...xs) - padX) / width);
  const y0 = Math.max(0, (Math.min(...ys) - padY) / height);
  const x1 = Math.min(1, (Math.max(...xs) + padX) / width);
  const y1 = Math.min(1, (Math.max(...ys) + padY) / height);
  if (x1 <= x0 || y1 <= y0) return null;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

export function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
