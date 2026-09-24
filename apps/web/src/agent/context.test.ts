import caPlanJson from "@samples/fixtures/ca-northwind.plan.json";
import { describe, expect, it } from "vitest";

import type { Plan } from "@/domain/types";

import { applyAliases, buildChatContext, byteLength, CONTEXT_MAX_BYTES } from "./context";

const caPlan = caPlanJson as unknown as Plan;

describe("buildChatContext", () => {
  it("sends only aliases, names and categories from the plan", () => {
    const context = buildChatContext({ plan: caPlan, today: "2026-09-16", tz: "America/Toronto" });
    expect(context).toEqual({
      region: "CA",
      today: "2026-09-16",
      tz: "America/Toronto",
      currency: "CAD",
      planName: "Group Extended Health and Dental, Class A",
      memberAliases: ["[MEMBER_A]", "[MEMBER_B]", "[MEMBER_C]"],
      categories: caPlan.categories.map((c) => c.name),
    });
    expect(byteLength(context)).toBeLessThanOrEqual(CONTEXT_MAX_BYTES);
  });

  it("stays within 2 KB for an oversized plan", () => {
    const huge: Plan = {
      ...caPlan,
      planName: "A".repeat(500),
      members: Array.from({ length: 30 }, (_, i) => ({ id: `m${i}`, alias: `[MEMBER_${i}_${"X".repeat(40)}]`, relationship: "child" })),
      categories: Array.from({ length: 80 }, (_, i) => ({
        id: `c${i}`,
        name: `Category number ${i} with a very long descriptive name that keeps going`,
        kind: "other",
        benefits: [],
      })),
    };
    const context = buildChatContext({ plan: huge, today: "2026-09-16", tz: "Australia/Sydney" });
    expect(byteLength(context)).toBeLessThanOrEqual(CONTEXT_MAX_BYTES);
    expect(context.planName!.length).toBeLessThanOrEqual(80);
    expect(context.memberAliases!.length).toBeGreaterThan(0);
  });

  it("falls back to region defaults without a plan", () => {
    expect(buildChatContext({ plan: null, today: "2026-09-16", tz: "Australia/Perth", region: "AU" })).toEqual({
      region: "AU",
      today: "2026-09-16",
      tz: "Australia/Perth",
      currency: "AUD",
      planName: null,
      memberAliases: [],
      categories: [],
    });
  });
});

describe("applyAliases", () => {
  const aliases = [
    { token: "[MEMBER_A]", values: ["Jordan Smith", "Jordan"] },
    { token: "[MEMBER_B]", values: ["Ann"] },
    { token: "[EMPLOYER_A]", values: ["Acme (Canada) Inc."] },
    { token: "[POLICY_1]", values: ["416-555-0142"] },
  ];

  it("replaces raw values case-insensitively, longest first", () => {
    expect(applyAliases("Can JORDAN SMITH and jordan claim?", aliases)).toBe("Can [MEMBER_A] and [MEMBER_A] claim?");
  });

  it("only replaces whole words", () => {
    expect(applyAliases("Ann's annual massage with Annabel", aliases)).toBe("[MEMBER_B]'s annual massage with Annabel");
  });

  it("matches numbers however they are punctuated", () => {
    expect(applyAliases("Policy 416 555 0142 and (416) 555-0142", aliases)).toBe("Policy [POLICY_1] and [POLICY_1]");
  });

  it("escapes values with regex characters and keeps tokens intact", () => {
    expect(applyAliases("I work at acme (canada) inc. as [MEMBER_A]", aliases)).toBe(
      "I work at [EMPLOYER_A] as [MEMBER_A]",
    );
  });

  it("leaves text alone without aliases", () => {
    expect(applyAliases("How much massage is left?", [])).toBe("How much massage is left?");
  });
});
