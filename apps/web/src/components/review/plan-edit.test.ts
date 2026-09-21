import { describe, expect, it } from "vitest";

import type { AnalyzeChunkResponse, Issue, Plan } from "@/domain/types";
import fixture from "@samples/fixtures/ca-northwind.plan.json";

import { planChunks } from "@/redaction/chunk-pdf";

import { runPool } from "./chunk-upload";
import {
  centsToDollarsInput,
  describeCoverage,
  describeLimit,
  dollarsToCents,
  flattenBenefits,
  mergeIssues,
  needsCheck,
  periodFor,
  rowSources,
  setBenefitPool,
  updateBenefit,
  withAliasMembers,
} from "./plan-edit";

const plan = fixture as unknown as Plan;

describe("plan edits", () => {
  it("flattens benefits with their category names", () => {
    const rows = flattenBenefits(plan);
    expect(rows.length).toBe(plan.categories.reduce((n, c) => n + c.benefits.length, 0));
    expect(rows[0].categoryName).toBe(plan.categories[0].name);
  });

  it("applies edits immutably", () => {
    const id = plan.categories[0].benefits[0].id;
    const next = updateBenefit(plan, id, (b) => ({ ...b, name: "Renamed" }));
    expect(flattenBenefits(next)[0].benefit.name).toBe("Renamed");
    expect(flattenBenefits(plan)[0].benefit.name).not.toBe("Renamed");
  });

  it("keeps pool membership in sync when a benefit moves", () => {
    const pool = plan.limitPools[0];
    const member = pool.benefitIds[0];
    const out = setBenefitPool(plan, member, null);
    expect(out.limitPools[0].benefitIds).not.toContain(member);
    const back = setBenefitPool(out, member, pool.id);
    expect(back.limitPools[0].benefitIds).toContain(member);
    expect(flattenBenefits(back).find((r) => r.benefit.id === member)?.benefit.poolId).toBe(pool.id);
  });

  it("parses dollars into cents", () => {
    expect(dollarsToCents("$1,500.50")).toBe(150050);
    expect(dollarsToCents("80")).toBe(8000);
    expect(dollarsToCents("")).toBeNull();
    expect(dollarsToCents("abc")).toBeNull();
    expect(centsToDollarsInput(8000)).toBe("80");
    expect(centsToDollarsInput(150050)).toBe("1500.50");
  });

  it("fills sensible defaults when the period kind changes", () => {
    const base = { kind: "benefit_year", startMonth: 1, startDay: 1, months: null, years: null } as const;
    expect(periodFor("rolling_months", base)).toEqual({ kind: "rolling_months", startMonth: null, startDay: null, months: 12, years: null });
    expect(periodFor("consecutive_benefit_years", base).years).toBe(2);
    expect(periodFor("benefit_year", base)).toBe(base);
  });

  it("describes coverage and limits", () => {
    const massage = plan.categories[0].benefits[0];
    expect(describeCoverage(massage.coverage, "CAD")).toBe("80% up to $80");
    expect(describeLimit(massage.limits[0], "CAD")).toBe("$500, benefit year");
    expect(describeLimit(undefined, "CAD")).toBe("No limit");
  });

  it("flags low-confidence and unsupported rows", () => {
    const b = plan.categories[0].benefits[0];
    expect(needsCheck({ ...b, source: { page: 1, quote: "q", confidence: 0.5, verifierVerdict: "supported" } })).toBe(true);
    expect(needsCheck({ ...b, source: { page: 1, quote: "q", confidence: 0.95, verifierVerdict: "unsupported" } })).toBe(true);
    expect(needsCheck({ ...b, source: { page: 1, quote: "q", confidence: 0.95, verifierVerdict: "supported" } })).toBe(false);
  });
});

describe("issues", () => {
  const advisory = (page: number): Issue => ({ code: "pii_advisory", severity: "info", message: `m${page}`, page, category: "Organization", rowId: null });

  it("removes duplicates reported by overlapping chunks and sorts by page", () => {
    const merged = mergeIssues([advisory(5), advisory(1)], [advisory(5)], [{ code: "duplicate_removed", severity: "info", message: "x", page: 3, category: null, rowId: null }]);
    expect(merged.map((i) => [i.code, i.page])).toEqual([
      ["pii_advisory", 1],
      ["duplicate_removed", 3],
      ["pii_advisory", 5],
    ]);
  });

  it("maps row ids to their quotes", () => {
    const response = {
      pages: [1],
      rows: {
        header: [],
        benefits: [{ row_id: "b1", page: 2, quote: "Massage 80%", meta: { rowId: "b1", kind: "benefit", page: 2, confidence: 0.4, grounded: true } }],
        pools: [],
        cost_shares: [],
        rules: [],
        hospital_categories: [],
      },
      issues: [],
      usage: {},
      traceId: "t",
    } as unknown as AnalyzeChunkResponse;
    expect(rowSources([response]).get("b1")).toEqual({ quote: "Massage 80%", page: 2, confidence: 0.4 });
  });
});

describe("runPool", () => {
  it("never runs more than the limit at once and runs everything", async () => {
    let active = 0;
    let peak = 0;
    const seen: number[] = [];
    const chunks = planChunks(37);
    await runPool(chunks, 4, async (chunk) => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 1 + (chunk[0] % 3)));
      seen.push(chunk[0]);
      active--;
    });
    expect(peak).toBe(4);
    expect(seen.sort((a, b) => a - b)).toEqual(chunks.map((c) => c[0]));
  });
});

describe("withAliasMembers", () => {
  const base = { ...plan, members: [], identifiers: [] } as Plan;
  const alias = (token: string, kind: "member" | "policy", relationship?: "self" | "spouse" | "child") => ({
    id: token,
    kind,
    token,
    values: ["x"],
    relationship,
    createdAt: "2026-09-17T00:00:00.000Z",
  });

  it("takes members and identifiers from the redaction aliases", () => {
    const out = withAliasMembers(base, [
      alias("[MEMBER_B]", "member", "spouse"),
      alias("[MEMBER_A]", "member", "self"),
      alias("[POLICY_1]", "policy"),
      alias("[CERT_1]", "policy"),
    ]);
    expect(out.members).toEqual([
      { id: "m-a", alias: "[MEMBER_A]", relationship: "self" },
      { id: "m-b", alias: "[MEMBER_B]", relationship: "spouse" },
    ]);
    expect(out.identifiers).toEqual(["[CERT_1]", "[POLICY_1]"]);
  });

  it("keeps extracted member ids and falls back to the policyholder", () => {
    const extracted = { ...base, members: [{ id: "x-1", alias: "[MEMBER_A]", relationship: "dependent" as const }] };
    expect(withAliasMembers(extracted, [alias("[MEMBER_A]", "member", "self")]).members).toEqual([
      { id: "x-1", alias: "[MEMBER_A]", relationship: "self" },
    ]);
    expect(withAliasMembers(base, []).members).toEqual([{ id: "m-a", alias: "[MEMBER_A]", relationship: "self" }]);
  });
});
