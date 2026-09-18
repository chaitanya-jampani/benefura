import { describe, expect, it } from "vitest";

import { AU_DEMO_PLAN as AU, CA_DEMO_PLAN as CA } from "../fixtures";
import { benefitUsage, computeUsage, countedClaims, memberHeadline } from "../usage";
import { makeClaim } from "./helpers";

const TODAY = "2026-09-16";

const massage = (status: "paid" | "submitted" | "draft" | "rejected", date: string, paid?: number) =>
  makeClaim(CA.id, "m-a", status, [{ benefitId: "ben-massage", date, charged: 12000 }], paid);

describe("counted claims", () => {
  it("counts paid and partially paid always, submitted on request, drafts and rejected never", () => {
    const claims = [
      massage("paid", "2026-01-10", 8000),
      makeClaim(CA.id, "m-a", "partially_paid", [{ benefitId: "ben-physio", date: "2026-01-11", charged: 9000 }], 5000),
      massage("submitted", "2026-02-10"),
      massage("draft", "2026-03-10"),
      massage("rejected", "2026-04-10", 0),
    ];
    expect(countedClaims(claims, { includeSubmitted: false }).map((c) => c.status)).toEqual(["paid", "partially_paid"]);
    expect(countedClaims(claims, { includeSubmitted: true }).map((c) => c.status)).toEqual(["paid", "partially_paid", "submitted"]);
  });
});

describe("benefit usage", () => {
  const history = [
    ...["2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15", "2026-05-15"].map((d) => massage("paid", d, 8000)),
    massage("submitted", "2026-08-20"),
    massage("draft", "2026-09-01"),
    massage("paid", "2025-11-15", 8000),
  ];

  it("tracks the massage maximum and the paramedical pool this benefit year", () => {
    const u = benefitUsage(CA, history, "ben-massage", "m-a", { today: TODAY, includeSubmitted: false });
    expect(u.limits[0]).toMatchObject({ used: 40000, remaining: 10000, window: { start: "2026-01-01", end: "2026-12-31" } });
    expect(u.pool).toMatchObject({ poolId: "pool-paramedical", used: 40000, remaining: 110000 });
    expect(u.remainingCents).toBe(10000);
    expect(u.usedCents).toBe(40000);

    const withSubmitted = benefitUsage(CA, history, "ben-massage", "m-a", { today: TODAY, includeSubmitted: true });
    expect(withSubmitted.limits[0]).toMatchObject({ used: 48000, remaining: 2000 });
  });

  it("reports the next eligible date for a used-up frequency", () => {
    const recall = makeClaim(CA.id, "m-c", "paid", [{ benefitId: "ben-dental-recall", date: "2026-04-20", charged: 18000 }], 16200);
    const u = benefitUsage(CA, [recall], "ben-dental-recall", "m-c", { today: TODAY, includeSubmitted: false });
    expect(u.frequency).toMatchObject({ count: 1, used: 1, nextEligible: "2027-01-20" });
    expect(u.remainingCents).toBe(200000 - 16200);
    const later = benefitUsage(CA, [recall], "ben-dental-recall", "m-c", { today: TODAY, includeSubmitted: false, asOf: "2027-01-20" });
    expect(later.frequency).toMatchObject({ used: 0, nextEligible: null });
  });

  it("keeps eyewear usage across the 2-year window but out of this year's paid total", () => {
    const glasses = makeClaim(CA.id, "m-b", "paid", [{ benefitId: "ben-eyewear", date: "2025-06-10", charged: 18000 }], 18000);
    const u = benefitUsage(CA, [glasses], "ben-eyewear", "m-b", { today: TODAY, includeSubmitted: false });
    expect(u.limits[0]).toMatchObject({ used: 18000, remaining: 12000, window: { start: "2025-01-01", end: "2026-12-31" } });
    expect(u.usedCents).toBe(0);
  });

  it("marks benefits still in their waiting period", () => {
    const u = benefitUsage(AU, [], "ben-major-dental", "m-a", { today: "2025-03-01", includeSubmitted: false });
    expect(u).toMatchObject({ inWaitingPeriod: true, waitingPeriodEnds: "2025-07-01" });
    expect(benefitUsage(AU, [], "ben-major-dental", "m-a", { today: TODAY, includeSubmitted: false }).inWaitingPeriod).toBe(false);
  });

  it("ignores claims from other plans and throws for unknown benefits", () => {
    const foreign = { ...massage("paid", "2026-01-10", 8000), planId: "other" };
    expect(benefitUsage(CA, [foreign], "ben-massage", "m-a", { today: TODAY, includeSubmitted: false }).usedCents).toBe(0);
    expect(() => benefitUsage(CA, [], "ben-unknown", "m-a", { today: TODAY, includeSubmitted: false })).toThrow(RangeError);
  });
});

describe("plan usage", () => {
  it("puts the family drug deductible under family and per-person limits under members", () => {
    const drugs = makeClaim(CA.id, "m-b", "paid", [{ benefitId: "ben-drugs", date: "2026-02-01", charged: 1800 }], 0);
    const usage = computeUsage(CA, [drugs], { today: TODAY, includeSubmitted: false });
    expect(usage.members.map((m) => m.memberId)).toEqual(["m-a", "m-b", "m-c"]);
    expect(usage.family.costShares).toEqual([
      { costShareId: "cs-drug-deductible", memberId: null, window: { start: "2026-01-01", end: "2026-12-31" }, metCents: 1800, remainingCents: 700 },
    ]);
    expect(usage.members[0].costShares).toEqual([]);
  });

  it("tracks the AU hospital excess per person", () => {
    const admission = makeClaim(AU.id, "m-b", "paid", [{ benefitId: "ben-hospital-admission", date: "2026-03-01", charged: 400000 }], 350000);
    const usage = computeUsage(AU, [admission], { today: TODAY, includeSubmitted: false });
    expect(usage.members[1].costShares[0]).toMatchObject({ memberId: "m-b", metCents: 50000, remainingCents: 0 });
    expect(usage.members[0].costShares[0]).toMatchObject({ memberId: "m-a", metCents: 0, remainingCents: 50000 });
  });

  it("builds the hero figure without double counting a shared pool", () => {
    const claims = [
      ...["2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15", "2026-05-15"].map((d) => massage("paid", d, 8000)),
      makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-physio", date: "2026-03-01", charged: 28500, quantity: 3 }], 22800),
      makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-dental-recall", date: "2026-04-20", charged: 24000 }], 21600),
      makeClaim(CA.id, "m-a", "paid", [{ benefitId: "ben-drugs", date: "2026-05-01", charged: 6000 }], 2800),
    ];
    const usage = computeUsage(CA, claims, { today: TODAY, includeSubmitted: false });
    const headline = memberHeadline(CA, usage.members[0]);
    // Paramedical: min(pool 150000 - 62800, massage 10000 + physio 52200) = 62200. Dental pool: 178400. Drugs: unlimited.
    expect(headline.remainingCents).toBe(62200 + 178400);
    expect(headline.paidCents).toBe(40000 + 22800 + 21600 + 2800);
    expect(headline.usedBenefitIds.sort()).toEqual(["ben-dental-recall", "ben-drugs", "ben-massage", "ben-physio"]);
  });
});
