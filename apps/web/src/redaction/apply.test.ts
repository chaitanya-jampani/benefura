import { describe, expect, it } from "vitest";

import type { AliasRecord, RedactionBox } from "@/db/dexie";

import { AliasBook, formatToken, mergeBoxes, normalizeToken, parseToken, rectFromPolygon } from "./apply";
import { applyFormToBook, EMPTY_FORM, formFromAliases } from "./known";

const NOW = () => "2026-09-16T00:00:00.000Z";

function box(partial: Partial<RedactionBox>): RedactionBox {
  return {
    id: Math.random().toString(36).slice(2),
    pageIndex: 0,
    x: 0.1,
    y: 0.1,
    w: 0.2,
    h: 0.02,
    token: "[MEMBER_A]",
    kind: "member",
    source: "detector",
    confidence: 0.9,
    enabled: true,
    ...partial,
  };
}

describe("tokens", () => {
  it("formats letter and number families", () => {
    expect(formatToken("MEMBER", 0)).toBe("[MEMBER_A]");
    expect(formatToken("MEMBER", 25)).toBe("[MEMBER_Z]");
    expect(formatToken("MEMBER", 26)).toBe("[MEMBER_AA]");
    expect(formatToken("POLICY", 0)).toBe("[POLICY_1]");
    expect(formatToken("ID", 9)).toBe("[ID_10]");
  });

  it("parses what it formats", () => {
    for (const [family, i] of [["MEMBER", 27], ["EMAIL", 0], ["CERT", 4], ["ID", 11]] as const) {
      expect(parseToken(formatToken(family, i))).toEqual({ family, index: i });
    }
    expect(parseToken("[MEMBER_1]")).toBeNull();
    expect(parseToken("[POLICY_A]")).toBeNull();
    expect(parseToken("MEMBER_A")).toBeNull();
  });

  it("normalizes typed tokens", () => {
    expect(normalizeToken("member a")).toBe("[MEMBER_A]");
    expect(normalizeToken("[cert_2]")).toBe("[CERT_2]");
    expect(normalizeToken("hello")).toBeNull();
  });
});

describe("AliasBook", () => {
  it("allocates the next free token per family and reuses tokens for repeated values", () => {
    const book = new AliasBook([], NOW);
    expect(book.tokenFor("ID", "046 454 286")).toBe("[ID_1]");
    expect(book.tokenFor("ID", "130-692-544")).toBe("[ID_2]");
    expect(book.tokenFor("ID", "046-454-286")).toBe("[ID_1]");
    expect(book.tokenFor("MEMBER", "Jordan Testcase")).toBe("[MEMBER_A]");
    expect(book.tokenFor("MEMBER", "JORDAN TESTCASE")).toBe("[MEMBER_A]");
  });

  it("starts from saved aliases and only reports changed records", () => {
    const saved: AliasRecord[] = [
      { id: "alias:member_a", kind: "member", token: "[MEMBER_A]", values: ["Jordan Testcase"], relationship: "self", createdAt: NOW() },
    ];
    const book = new AliasBook(saved, NOW);
    expect(book.tokenFor("MEMBER", "Jordan Testcase")).toBe("[MEMBER_A]");
    expect(book.changed().put).toEqual([]);
    expect(book.tokenFor("MEMBER", "Riley Testcase")).toBe("[MEMBER_B]");
    expect(book.changed().put.map((r) => r.token)).toEqual(["[MEMBER_B]"]);
  });

  it("moves a value to a new token when reassigned and drops emptied records", () => {
    const book = new AliasBook([], NOW);
    book.tokenFor("MEMBER", "J. Testcase");
    book.assign("[MEMBER_B]", "member", "J. Testcase");
    const { put, remove } = book.changed();
    expect(put.map((r) => r.token)).toEqual(["[MEMBER_B]"]);
    expect(remove).toEqual(["alias:member_a"]);
    expect(book.lookup("j testcase")).toBe("[MEMBER_B]");
  });

  it("exposes saved values as detector inputs, never as token text", () => {
    const book = new AliasBook([], NOW);
    book.assign("[MEMBER_A]", "member", "Jordan Testcase", "self");
    book.assign("[PHONE_A]", "phone", "416-555-0199");
    expect(book.knownValues()).toEqual([
      { token: "[MEMBER_A]", kind: "member", value: "Jordan Testcase", isName: true },
      { token: "[PHONE_A]", kind: "phone", value: "416-555-0199", isName: false },
    ]);
  });
});

describe("form tokens", () => {
  it("assigns default tokens and skips tokens already used by other values", () => {
    const saved: AliasRecord[] = [
      { id: "alias:member_a", kind: "member", token: "[MEMBER_A]", values: ["Sam Earlier"], createdAt: NOW() },
    ];
    const book = new AliasBook(saved, NOW);
    const { known, tokens } = applyFormToBook(
      {
        ...EMPTY_FORM,
        selfName: "Jordan Testcase",
        family: [{ id: "f1", name: "Riley Testcase", relationship: "spouse" }],
        policyNumber: "88213",
      },
      book,
    );
    expect(tokens.fields.selfName).toBe("[MEMBER_B]");
    expect(tokens.family.f1).toBe("[MEMBER_C]");
    expect(tokens.fields.policyNumber).toBe("[POLICY_1]");
    expect(tokens.fields.certificateNumber).toBe("[CERT_1]");
    // Saved values are hidden too.
    expect(known.map((k) => k.value)).toEqual(expect.arrayContaining(["Jordan Testcase", "Riley Testcase", "88213", "Sam Earlier"]));
  });

  it("round-trips the form through saved aliases", () => {
    const book = new AliasBook([], NOW);
    applyFormToBook({ ...EMPTY_FORM, selfName: "Jordan Testcase", email: "j@example.com", family: [{ id: "x", name: "Riley", relationship: "child" }] }, book);
    let n = 0;
    const form = formFromAliases(book.all(), () => `id${n++}`);
    expect(form.selfName).toBe("Jordan Testcase");
    expect(form.email).toBe("j@example.com");
    expect(form.family).toEqual([{ id: "id0", name: "Riley", relationship: "child" }]);
  });
});

describe("mergeBoxes", () => {
  it("dedupes overlapping boxes with the same token", () => {
    const merged = mergeBoxes([box({ x: 0.1, w: 0.2 }), box({ x: 0.11, w: 0.2, confidence: 0.99 })]);
    expect(merged).toHaveLength(1);
    expect(merged[0].x).toBeCloseTo(0.1);
    expect(merged[0].w).toBeCloseTo(0.21);
    expect(merged[0].confidence).toBe(0.99);
  });

  it("joins same-token neighbours on a line but keeps different tokens apart", () => {
    const joined = mergeBoxes([box({ x: 0.1, w: 0.08 }), box({ x: 0.185, w: 0.1 })]);
    expect(joined).toHaveLength(1);
    const apart = mergeBoxes([box({ x: 0.1, w: 0.08 }), box({ x: 0.185, w: 0.1, token: "[MEMBER_B]" })]);
    expect(apart).toHaveLength(2);
    const otherLine = mergeBoxes([box({ y: 0.1 }), box({ y: 0.2 })]);
    expect(otherLine).toHaveLength(2);
  });

  it("never merges manual boxes", () => {
    expect(mergeBoxes([box({ source: "manual" }), box({ source: "manual" })])).toHaveLength(2);
  });
});

describe("rectFromPolygon", () => {
  it("converts a CU polygon in inches to page fractions with padding", () => {
    const r = rectFromPolygon([1, 2, 3, 2, 3, 2.25, 1, 2.25], { width: 8.5, height: 11 })!;
    expect(r.x).toBeCloseTo(0.95 / 8.5);
    expect(r.y).toBeCloseTo(1.95 / 11);
    expect(r.w).toBeCloseTo(2.1 / 8.5);
    expect(r.h).toBeCloseTo(0.35 / 11);
  });

  it("treats large coordinates as pixels for images", () => {
    const r = rectFromPolygon([300, 600, 900, 600, 900, 675, 300, 675], { width: 8.5, height: 11 }, { width: 2550, height: 3300 })!;
    expect(r.x).toBeCloseTo((300 - 15) / 2550);
    expect(r.w).toBeCloseTo(630 / 2550);
  });

  it("rejects malformed polygons", () => {
    expect(rectFromPolygon(null, { width: 8.5, height: 11 })).toBeNull();
    expect(rectFromPolygon([1, 2, 3], { width: 8.5, height: 11 })).toBeNull();
  });
});
