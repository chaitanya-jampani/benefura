import caPlanJson from "@samples/fixtures/ca-northwind.plan.json";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { db, SETTING_KEYS, setSetting } from "@/db/dexie";
import type { Claim, Plan } from "@/domain/types";

vi.mock("@/domain/search", () => ({
  findBenefits: vi.fn(() => [
    { benefitId: "ben-massage", categoryId: "cat-paramedical", name: "Massage therapy", categoryName: "Paramedical practitioners", score: 3 },
  ]),
  searchPlanDocument: vi.fn(() => [{ page: 5, quote: "Massage therapy: 80% up to $80 per visit", benefitId: "ben-massage", score: 2 }]),
}));
vi.mock("@/domain/usage", () => ({
  benefitUsage: vi.fn((_plan: Plan, _claims: Claim[], benefitId: string, memberId: string) => ({
    benefitId,
    memberId,
    limits: [
      {
        limit: { unit: "cents", value: 50000, period: { kind: "benefit_year" }, scope: "per_person" },
        window: { start: "2026-01-01", end: "2026-12-31" },
        used: 8000,
        remaining: 42000,
      },
    ],
    pool: null,
    frequency: null,
    remainingCents: 42000,
    usedCents: 8000,
    waitingPeriodEnds: null,
    inWaitingPeriod: false,
  })),
}));
vi.mock("@/domain/estimate", () => ({
  estimateReimbursement: vi.fn(() => ({
    benefitId: "ben-massage",
    eligibleCents: 12000,
    planPaysCents: 8000,
    memberPaysCents: 4000,
    limitedBy: "per_service_cap",
    steps: [{ label: "80% coverage", planPaysCents: 9600 }, { label: "Capped at $80 per visit", planPaysCents: 8000 }],
    warnings: [],
    remainingAfter: { limitCents: 34000, poolCents: 142000 },
    deadline: "2027-03-31",
  })),
}));
vi.mock("@/domain/claims", () => ({
  createDraftClaim: vi.fn((plan: Plan, input: { patientMemberId: string; provider?: string | null; lines: Omit<Claim["lines"][number], "id">[] }, actor: Record<string, unknown>, now: Date) => ({
    id: "clm_new",
    planId: plan.id,
    status: "draft",
    patientMemberId: input.patientMemberId,
    provider: input.provider ?? null,
    lines: input.lines.map((l, i) => ({ ...l, id: `line_${i}` })),
    outcome: null,
    attachments: [],
    history: [{ status: "draft", at: now.toISOString(), ...actor, note: null }],
    deadline: "2027-03-31",
    createdAt: now.toISOString(),
    updatedAt: now.toISOString(),
  })),
  updateClaim: vi.fn((_plan: Plan, claim: Claim, patch: Partial<Claim>, actor: Record<string, unknown>, now: Date) => ({
    ...claim,
    ...patch,
    history: [...claim.history, { status: patch.status ?? claim.status, at: now.toISOString(), ...actor, note: null }],
    updatedAt: now.toISOString(),
  })),
  claimTotals: vi.fn((claim: Claim) => ({
    chargedCents: claim.lines.reduce((sum, l) => sum + l.chargedCents, 0),
    paidCents: claim.outcome?.paidCents ?? null,
  })),
}));
vi.mock("@/domain/periods", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/domain/periods")>()),
  currentBenefitPeriod: vi.fn(() => ({ start: "2026-01-01", end: "2026-12-31" })),
}));

const { createDraftClaim, updateClaim } = await import("@/domain/claims");
const { estimateReimbursement } = await import("@/domain/estimate");
const { runBrowserTool, ToolError, APPROVAL_TOOLS, BROWSER_TOOLS } = await import("./tools");

const plan = caPlanJson as unknown as Plan;
const NOW = new Date("2026-09-16T15:00:00Z");
const provenance = { agentName: "benefura-plan-claims", agentVersion: "4", traceId: "4bf92f3577b34da6a3ce929d0e0e4736" };

function existingClaim(overrides: Partial<Claim> = {}): Claim {
  return {
    id: "clm_1",
    planId: plan.id,
    status: "submitted",
    patientMemberId: "m-b",
    provider: "[PROVIDER_1]",
    lines: [
      { id: "l1", serviceDate: "2026-08-02", benefitId: "ben-physio", itemCode: null, description: null, quantity: 1, chargedCents: 9000, otherPlanPaidCents: 0 },
    ],
    outcome: null,
    attachments: [],
    history: [],
    deadline: "2027-03-31",
    createdAt: "2026-08-02T10:00:00Z",
    updatedAt: "2026-08-03T10:00:00Z",
    ...overrides,
  };
}

beforeEach(async () => {
  await db.open();
  await Promise.all([db.plans.clear(), db.claims.clear(), db.settings.clear(), db.aliases.clear()]);
  await db.plans.put({ id: plan.id, plan, isDemo: true, createdAt: "2026-09-01T00:00:00Z", updatedAt: "2026-09-01T00:00:00Z" });
  await setSetting(SETTING_KEYS.activePlanId, plan.id);
  await db.aliases.put({ id: "a1", kind: "member", token: "[MEMBER_A]", values: ["Jordan Rivera"], createdAt: "2026-09-01T00:00:00Z" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("browser tool registry", () => {
  it("marks only record-changing tools for approval", () => {
    expect([...APPROVAL_TOOLS].sort()).toEqual(["draft_claim", "update_claim"]);
    expect(BROWSER_TOOLS.size).toBe(8);
  });
});

describe("read-only tools", () => {
  it("get_plan_overview is compact and aliased", async () => {
    const output = (await runBrowserTool("get_plan_overview", {}, { now: NOW, tz: "America/Toronto" })) as Record<string, unknown>;
    expect(output).toMatchObject({
      insurer: "Northwind Life & Health",
      region: "CA",
      currency: "CAD",
      benefitPeriod: { start: "2026-01-01", end: "2026-12-31" },
      members: [
        { alias: "[MEMBER_A]", relationship: "self" },
        { alias: "[MEMBER_B]", relationship: "spouse" },
        { alias: "[MEMBER_C]", relationship: "child" },
      ],
    });
    const json = JSON.stringify(output);
    expect(json).not.toContain("Jordan");
    expect(new TextEncoder().encode(json).byteLength).toBeLessThan(4000);
  });

  it("find_benefits describes coverage, limits and the booklet source", async () => {
    const output = await runBrowserTool("find_benefits", { query: "RMT" });
    expect(output).toEqual({
      query: "RMT",
      benefits: [
        {
          benefitId: "ben-massage",
          name: "Massage therapy",
          category: "Paramedical practitioners",
          coverage: "80% up to $80.00 per visit",
          limits: ["$500.00 per person per benefit year"],
          pool: expect.stringMatching(/^Paramedical combined maximum: \$1.500\.00 per person per benefit year$/),
          frequency: null,
          waitingPeriodMonths: null,
          requirements: ["Treatment by a registered massage therapist"],
          source: { page: 5, quote: expect.stringContaining("80% up to $80 per visit") },
        },
      ],
    });
  });

  it("search_plan_document returns pages and quotes", async () => {
    expect(await runBrowserTool("search_plan_document", { query: "massage" })).toEqual({
      query: "massage",
      results: [{ page: 5, quote: "Massage therapy: 80% up to $80 per visit", benefitId: "ben-massage" }],
    });
  });

  it("get_usage resolves member aliases loosely", async () => {
    const output = (await runBrowserTool("get_usage", { benefit_id: "ben-massage", member_alias: "member_a" })) as {
      usage: Array<Record<string, unknown>>;
    };
    expect(output.usage).toEqual([
      expect.objectContaining({
        benefitId: "ben-massage",
        memberAlias: "[MEMBER_A]",
        remainingCents: 42000,
        usedCents: 8000,
        limits: [expect.objectContaining({ limit: "$500.00 per person per benefit year", remaining: 42000 })],
      }),
    ]);
  });

  it("get_usage covers every member when no alias is given", async () => {
    const output = (await runBrowserTool("get_usage", { benefit_id: "ben-massage", member_alias: null })) as {
      usage: Array<{ memberAlias: string }>;
    };
    expect(output.usage.map((u) => u.memberAlias)).toEqual(["[MEMBER_A]", "[MEMBER_B]", "[MEMBER_C]"]);
  });

  it("rejects unknown members and benefits with actionable errors", async () => {
    await expect(runBrowserTool("get_usage", { benefit_id: "ben-massage", member_alias: "[MEMBER_Z]" })).rejects.toThrow(
      /Members on this plan: \[MEMBER_A\], \[MEMBER_B\], \[MEMBER_C\]/,
    );
    await expect(runBrowserTool("get_usage", { benefit_id: "ben-nope", member_alias: null })).rejects.toBeInstanceOf(ToolError);
  });

  it("estimate_reimbursement passes engine-exact inputs", async () => {
    const output = await runBrowserTool("estimate_reimbursement", {
      benefit_id: "ben-massage",
      member_alias: "[MEMBER_A]",
      service_date: "2026-09-12",
      charged_cents: 12000,
      quantity: null,
      item_code: null,
    });
    expect(estimateReimbursement).toHaveBeenCalledWith(
      plan,
      [],
      { benefitId: "ben-massage", memberId: "m-a", serviceDate: "2026-09-12", chargedCents: 12000, quantity: undefined, itemCode: null, otherPlanPaidCents: 0 },
      expect.objectContaining({ includeSubmitted: false }),
    );
    expect(output).toMatchObject({ planPaysCents: 8000, memberPaysCents: 4000, limitedBy: "per_service_cap" });
    await expect(
      runBrowserTool("estimate_reimbursement", {
        benefit_id: "ben-massage",
        member_alias: "[MEMBER_A]",
        service_date: "12/09/2026",
        charged_cents: 120.5,
        quantity: null,
        item_code: null,
      }),
    ).rejects.toThrow(/ISO date/);
  });

  it("list_claims filters and summarizes with aliases", async () => {
    await db.claims.bulkPut([existingClaim(), existingClaim({ id: "clm_2", status: "paid", updatedAt: "2026-09-01T00:00:00Z" })]);
    const output = await runBrowserTool("list_claims", { status: "submitted", benefit_id: null });
    expect(output).toEqual({
      total: 1,
      claims: [
        {
          claimId: "clm_1",
          status: "submitted",
          memberAlias: "[MEMBER_B]",
          provider: "[PROVIDER_1]",
          serviceDates: ["2026-08-02"],
          benefitIds: ["ben-physio"],
          chargedCents: 9000,
          paidCents: null,
          deadline: "2027-03-31",
          updatedAt: "2026-08-03T10:00:00Z",
        },
      ],
    });
  });

  it("fails clearly when no plan is loaded", async () => {
    await db.plans.clear();
    await db.settings.clear();
    await expect(runBrowserTool("get_plan_overview", {})).rejects.toThrow(/No plan is loaded/);
  });
});

describe("tools that change records", () => {
  it("draft_claim stores a draft with agent provenance", async () => {
    const output = await runBrowserTool(
      "draft_claim",
      {
        member_alias: "[MEMBER_A]",
        provider: null,
        lines: [{ benefit_id: "ben-massage", service_date: "2026-09-12", charged_cents: 12000, quantity: null, item_code: null, description: null }],
      },
      { provenance, now: NOW },
    );
    expect(createDraftClaim).toHaveBeenCalledWith(
      plan,
      {
        patientMemberId: "m-a",
        provider: null,
        lines: [{ benefitId: "ben-massage", serviceDate: "2026-09-12", chargedCents: 12000, quantity: 1, itemCode: null, description: null, otherPlanPaidCents: 0 }],
      },
      { actor: "agent", ...provenance },
      NOW,
    );
    expect(output).toEqual({
      claimId: "clm_new",
      status: "draft",
      memberAlias: "[MEMBER_A]",
      provider: null,
      lines: 1,
      totalChargedCents: 12000,
      deadline: "2027-03-31",
    });
    const stored = await db.claims.get("clm_new");
    expect(stored?.planId).toBe(plan.id);
    expect(stored?.history[0]).toMatchObject({ actor: "agent", agentName: "benefura-plan-claims", agentVersion: "4", traceId: provenance.traceId });
  });

  it("update_claim records the outcome with provenance", async () => {
    await db.claims.put(existingClaim());
    const output = await runBrowserTool(
      "update_claim",
      { claim_id: "clm_1", status: "paid", paid_cents: 7200, provider: null, note: "Paid by EFT" },
      { provenance, now: NOW, tz: "UTC" },
    );
    expect(updateClaim).toHaveBeenCalledWith(
      plan,
      expect.objectContaining({ id: "clm_1" }),
      { status: "paid", outcome: { paidCents: 7200, decidedOn: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/), note: "Paid by EFT" } },
      { actor: "agent", ...provenance },
      NOW,
    );
    expect(output).toMatchObject({ claimId: "clm_1", status: "paid", paidCents: 7200 });
    expect((await db.claims.get("clm_1"))?.status).toBe("paid");
  });

  it("update_claim refuses unknown claims and empty patches", async () => {
    await db.claims.put(existingClaim());
    await expect(
      runBrowserTool("update_claim", { claim_id: "clm_x", status: "paid", paid_cents: null, provider: null, note: null }),
    ).rejects.toThrow(/Unknown claim id/);
    await expect(
      runBrowserTool("update_claim", { claim_id: "clm_1", status: null, paid_cents: null, provider: null, note: null }),
    ).rejects.toThrow(/Nothing to update/);
  });

  it("refuses non-browser tools", async () => {
    await expect(runBrowserTool("search_public_knowledge", {})).rejects.toThrow(/not a browser tool/);
  });
});
