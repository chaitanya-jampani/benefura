import { describe, expect, it } from "vitest";

import { estimateClaim, estimateReimbursement, type EstimateInput } from "../estimate";
import { AU_DEMO_PLAN as AU, CA_DEMO_PLAN as CA } from "../fixtures";
import { percentOf, roundHalfUp, allocate } from "../money";
import type { Claim, Plan } from "../types";
import { makeClaim } from "./helpers";

const TODAY = "2026-09-16";
const opts = { today: TODAY, includeSubmitted: false };

function est(plan: Plan, claims: Claim[], input: Partial<EstimateInput> & Pick<EstimateInput, "benefitId" | "chargedCents">) {
  return estimateReimbursement(plan, claims, { memberId: "m-a", serviceDate: TODAY, ...input }, opts);
}

describe("rounding", () => {
  it("rounds percentages half up to the cent", () => {
    expect(percentOf(12345, 50)).toBe(6173);
    expect(percentOf(63, 80)).toBe(50);
    expect(percentOf(25, 50)).toBe(13);
    expect(percentOf(10001, 62.5)).toBe(6251);
    expect(roundHalfUp(5, 2)).toBe(3);
    expect(allocate(1000, [1, 1, 1])).toEqual([334, 333, 333]);
    expect(allocate(500, [0, 0])).toEqual([500, 0]);
  });

  it("applies half-up rounding in estimates (CA major dental 50% of $123.45)", () => {
    const r = est(CA, [], { benefitId: "ben-dental-major", chargedCents: 12345, serviceDate: "2026-02-01" });
    expect(r.planPaysCents).toBe(6173);
    expect(r.memberPaysCents).toBe(6172);
  });
});

describe("Canada: Northwind", () => {
  it("pays massage at 80% capped at $80 a visit", () => {
    const r = est(CA, [], { benefitId: "ben-massage", chargedCents: 12000 });
    expect(r.planPaysCents).toBe(8000);
    expect(r.memberPaysCents).toBe(4000);
    expect(r.limitedBy).toBe("per_service_cap");
    expect(r.remainingAfter).toEqual({ limitCents: 42000, poolCents: 142000 });
    expect(r.deadline).toBe("2027-03-31");
    expect(r.steps.map((s) => s.planPaysCents)).toEqual([9600, 8000, 8000, 8000]);
  });

  it("stops massage at the $500 benefit maximum", () => {
    const history = Array.from({ length: 6 }, (_, i) =>
      makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-massage", date: `2026-0${i + 1}-15`, charged: 12000 }], 8000),
    );
    const r = est(CA, history, { benefitId: "ben-massage", chargedCents: 12000 });
    expect(r.planPaysCents).toBe(2000);
    expect(r.limitedBy).toBe("limit");
    expect(r.remainingAfter).toEqual({ limitCents: 0, poolCents: 100000 });
  });

  it("stops at the $1,500 paramedical pool even when the massage maximum has room", () => {
    const history = [
      makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-physio", date: "2026-02-01", charged: 93750 }], 75000),
      makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-chiro", date: "2026-03-01", charged: 62500 }], 50000),
      makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-naturopath", date: "2026-04-01", charged: 25000 }], 20000),
    ];
    const r = est(CA, history, { benefitId: "ben-massage", chargedCents: 10000 });
    expect(r.planPaysCents).toBe(5000);
    expect(r.limitedBy).toBe("pool");
    expect(r.remainingAfter).toEqual({ limitCents: 45000, poolCents: 0 });
    expect(est(CA, history, { benefitId: "ben-massage", chargedCents: 10000, memberId: "m-b" }).planPaysCents).toBe(8000);
  });

  it("applies the $25 drug deductible once per family", () => {
    const first = est(CA, [], { benefitId: "ben-drugs", chargedCents: 4250 });
    expect(first.planPaysCents).toBe(1400);
    expect(first.memberPaysCents).toBe(2850);
    expect(first.limitedBy).toBe("deductible");

    const paidByA = makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-drugs", date: "2026-02-01", charged: 4250 }], 1400);
    const spouse = est(CA, [paidByA], { benefitId: "ben-drugs", chargedCents: 6000, memberId: "m-b" });
    expect(spouse.planPaysCents).toBe(4800);
    expect(spouse.limitedBy).toBe("coverage");
    expect(spouse.steps.some((s) => s.label === "Drug deductible already met")).toBe(true);

    const partial = makeClaim(CA.id, "m-c", "paid", [{ benefitId: "ben-drugs", date: "2026-02-01", charged: 1000 }], 0);
    const rest = est(CA, [partial], { benefitId: "ben-drugs", chargedCents: 3000, memberId: "m-b" });
    expect(rest.planPaysCents).toBe(1200);

    const nextYear = est(CA, [paidByA], { benefitId: "ben-drugs", chargedCents: 4250, serviceDate: "2027-01-05" });
    expect(nextYear.planPaysCents).toBe(1400);
  });

  it("limits eyewear to $300 across 2 consecutive benefit years", () => {
    const glasses = makeClaim(CA.id, "m-b", "paid", [{ benefitId: "ben-eyewear", date: "2025-06-10", charged: 18000 }], 18000);
    const now = est(CA, [glasses], { benefitId: "ben-eyewear", chargedCents: 20000, memberId: "m-b" });
    expect(now.planPaysCents).toBe(12000);
    expect(now.limitedBy).toBe("limit");
    const nextWindow = est(CA, [glasses], { benefitId: "ben-eyewear", chargedCents: 20000, memberId: "m-b", serviceDate: "2027-01-15" });
    expect(nextWindow.planPaysCents).toBe(20000);
    expect(nextWindow.limitedBy).toBeNull();
  });

  it("allows one recall exam every 9 months", () => {
    const recall = makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-dental-recall", date: "2026-02-10", charged: 20000 }], 18000);
    const early = est(CA, [recall], { benefitId: "ben-dental-recall", chargedCents: 20000 });
    expect(early.planPaysCents).toBe(0);
    expect(early.limitedBy).toBe("frequency");
    expect(early.warnings).toContain("Not eligible again until Nov 10, 2026.");
    expect(est(CA, [recall], { benefitId: "ben-dental-recall", chargedCents: 20000, serviceDate: "2026-11-09" }).planPaysCents).toBe(0);
    const onTime = est(CA, [recall], { benefitId: "ben-dental-recall", chargedCents: 20000, serviceDate: "2026-11-10" });
    expect(onTime.planPaysCents).toBe(18000);
    expect(onTime.remainingAfter.poolCents).toBe(200000 - 18000 - 18000);
  });

  it("waits 12 months for major dental, then pays 50%", () => {
    const waiting = est(CA, [], { benefitId: "ben-dental-major", chargedCents: 100000, serviceDate: "2025-10-01" });
    expect(waiting.planPaysCents).toBe(0);
    expect(waiting.limitedBy).toBe("waiting_period");
    const covered = est(CA, [], { benefitId: "ben-dental-major", chargedCents: 100000, serviceDate: "2026-02-01" });
    expect(covered.planPaysCents).toBe(50000);
    expect(covered.limitedBy).toBe("coverage");
  });

  it("pays only the balance after another plan", () => {
    const r = est(CA, [], { benefitId: "ben-physio", chargedCents: 10000, otherPlanPaidCents: 6000 });
    expect(r.planPaysCents).toBe(4000);
    expect(r.memberPaysCents).toBe(0);
    expect(r.limitedBy).toBe("other_plan");
  });

  it("caps eye exams at $100 and blocks a second within 24 months", () => {
    expect(est(CA, [], { benefitId: "ben-eye-exam", chargedCents: 14000 }).planPaysCents).toBe(10000);
    const exam = makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-eye-exam", date: "2025-07-01", charged: 12000 }], 10000);
    expect(est(CA, [exam], { benefitId: "ben-eye-exam", chargedCents: 12000 }).limitedBy).toBe("frequency");
  });

  it("counts compression stockings by item", () => {
    const pairs = makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-compression", date: "2026-03-01", charged: 12000, quantity: 1 }], 9600);
    const r = est(CA, [pairs], { benefitId: "ben-compression", chargedCents: 24000, quantity: 2 });
    expect(r.planPaysCents).toBe(9600);
    expect(r.limitedBy).toBe("limit");
  });

  it("counts submitted claims only when asked", () => {
    const submitted = makeClaim(CA.id, "m-a", "submitted", [{ benefitId: "ben-naturopath", date: "2026-05-01", charged: 30000 }]);
    const input = { memberId: "m-a", serviceDate: TODAY, benefitId: "ben-naturopath", chargedCents: 30000 };
    expect(estimateReimbursement(CA, [submitted], input, opts).planPaysCents).toBe(24000);
    expect(estimateReimbursement(CA, [submitted], input, { ...opts, includeSubmitted: true }).planPaysCents).toBe(6000);
    expect(
      estimateReimbursement(CA, [submitted], input, { ...opts, includeSubmitted: true, excludeClaimId: submitted.id }).planPaysCents,
    ).toBe(24000);
  });

  it("rejects benefits that aren't in the plan and services before coverage", () => {
    expect(est(CA, [], { benefitId: "ben-nope", chargedCents: 1000 }).limitedBy).toBe("not_covered");
    expect(est(CA, [], { benefitId: "ben-physio", chargedCents: 1000, serviceDate: "2024-12-31" }).planPaysCents).toBe(0);
  });

  it("estimates a multi-line claim with later lines seeing earlier ones", () => {
    const result = estimateClaim(
      CA,
      [],
      {
        patientMemberId: "m-a",
        lines: [
          { benefitId: "ben-massage", serviceDate: "2026-09-10", chargedCents: 60000, quantity: 6, itemCode: null, description: null, otherPlanPaidCents: 0 },
          { benefitId: "ben-massage", serviceDate: "2026-09-01", chargedCents: 12000, quantity: 1, itemCode: null, description: null, otherPlanPaidCents: 0 },
        ],
      },
      opts,
    );
    expect(result.lines[1].planPaysCents).toBe(8000);
    expect(result.lines[0].planPaysCents).toBe(42000);
    expect(result.planPaysCents).toBe(50000);
    expect(result.deadline).toBe("2027-03-31");
  });
});

describe("Australia: Wattle", () => {
  it("pays item 505 from the schedule inside the $700 therapies pool", () => {
    const r = est(AU, [], { benefitId: "ben-physio", chargedCents: 8500, itemCode: "505" });
    expect(r.planPaysCents).toBe(4500);
    expect(r.limitedBy).toBe("schedule");
    expect(r.remainingAfter).toEqual({ limitCents: null, poolCents: 65500 });
    expect(r.deadline).toBe("2028-09-15");

    const history = [
      makeClaim(AU.id, "m-a", "paid", [
        { benefitId: "ben-physio", date: "2026-02-02", charged: 9500, itemCode: "500" },
        { benefitId: "ben-physio", date: "2026-03-02", charged: 85000, itemCode: "505", quantity: 10 },
      ], 50500),
      makeClaim(AU.id, "m-a", "paid", [{ benefitId: "ben-remedial-massage", date: "2026-04-02", charged: 36000, itemCode: "205", quantity: 4 }], 14000),
      makeClaim(AU.id, "m-a", "paid", [{ benefitId: "ben-chiro", date: "2026-05-02", charged: 7500, itemCode: "1505" }], 4000),
    ];
    const pooled = est(AU, history, { benefitId: "ben-physio", chargedCents: 8500, itemCode: "item 505" });
    expect(pooled.planPaysCents).toBe(1500);
    expect(pooled.limitedBy).toBe("pool");
    expect(pooled.remainingAfter.poolCents).toBe(0);
  });

  it("needs a scheduled item number for general dental", () => {
    const r = est(AU, [], { benefitId: "ben-general-dental", chargedCents: 9000 });
    expect(r.planPaysCents).toBe(0);
    expect(r.limitedBy).toBe("schedule");
    expect(r.warnings[0]).toContain("011, 012, 022, 114, 121");
    expect(est(AU, [], { benefitId: "ben-general-dental", chargedCents: 6500, itemCode: "012" }).planPaysCents).toBe(4000);
  });

  it("pays psychology at 60% capped at $80 a session", () => {
    const capped = est(AU, [], { benefitId: "ben-psychology", chargedCents: 22000 });
    expect(capped.planPaysCents).toBe(8000);
    expect(capped.limitedBy).toBe("per_service_cap");
    const under = est(AU, [], { benefitId: "ben-psychology", chargedCents: 12341 });
    expect(under.planPaysCents).toBe(7405);
    expect(under.limitedBy).toBe("coverage");
  });

  it("applies the 12-month major dental waiting period", () => {
    const waiting = est(AU, [], { benefitId: "ben-major-dental", chargedCents: 150000, serviceDate: "2025-06-30" });
    expect(waiting.planPaysCents).toBe(0);
    expect(waiting.limitedBy).toBe("waiting_period");
    expect(waiting.warnings).toEqual(["Major dental is covered for services from 1 July 2025."]);
    const served = est(AU, [], { benefitId: "ben-major-dental", chargedCents: 150000, serviceDate: "2025-07-01" });
    expect(served.planPaysCents).toBe(90000);
    const limited = est(AU, [], { benefitId: "ben-major-dental", chargedCents: 200000 });
    expect(limited.planPaysCents).toBe(100000);
    expect(limited.limitedBy).toBe("limit");
  });

  it("pays emergency ambulance in full", () => {
    const r = est(AU, [], { benefitId: "ben-ambulance", chargedCents: 120000 });
    expect(r.planPaysCents).toBe(120000);
    expect(r.limitedBy).toBeNull();
    expect(r.memberPaysCents).toBe(0);
  });

  it("takes the hospital excess first and flags hospital estimates as a guide", () => {
    const r = est(AU, [], { benefitId: "ben-hospital-admission", chargedCents: 300000 });
    expect(r.planPaysCents).toBe(250000);
    expect(r.limitedBy).toBe("deductible");
    expect(r.warnings.some((w) => w.startsWith("Hospital estimates are a guide"))).toBe(true);
  });
});
