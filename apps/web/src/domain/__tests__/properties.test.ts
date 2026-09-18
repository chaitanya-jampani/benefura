import fc from "fast-check";
import { describe, expect, it } from "vitest";

import { buildLedger, estimateReimbursement } from "../estimate";
import { AU_DEMO_PLAN as AU, CA_DEMO_PLAN as CA } from "../fixtures";
import { allBenefits } from "../ledger";
import { addDays, addMonths, claimDeadline, daysBetween, inRange, periodWindow } from "../periods";
import type { Claim, ClaimStatus, Period, Plan } from "../types";
import { benefitUsage, computeUsage } from "../usage";
import { makeClaim } from "./helpers";

const TODAY = "2026-09-16";
const BASE = "2024-01-01";

const planArb = fc.constantFrom(CA, AU);
const dateArb = fc.integer({ min: 0, max: 365 * 4 }).map((n) => addDays(BASE, n));

interface LineShape {
  benefitIndex: number;
  memberIndex: number;
  date: string;
  charged: number;
  quantity: number;
  itemIndex: number;
  otherRatio: number;
}

const lineArb: fc.Arbitrary<LineShape> = fc.record({
  benefitIndex: fc.nat(),
  memberIndex: fc.nat(),
  date: dateArb,
  charged: fc.integer({ min: 0, max: 400_000 }),
  quantity: fc.integer({ min: 1, max: 3 }),
  itemIndex: fc.nat(),
  otherRatio: fc.oneof(fc.constant(0), fc.double({ min: 0, max: 1, noNaN: true })),
});

function toInput(plan: Plan, shape: LineShape) {
  const benefits = allBenefits(plan);
  const benefit = benefits[shape.benefitIndex % benefits.length];
  const member = plan.members[shape.memberIndex % plan.members.length];
  const items = benefit.coverage.scheduleItems;
  const itemCode = items.length ? items[shape.itemIndex % (items.length + 1)]?.itemCode ?? null : null;
  return {
    benefitId: benefit.id,
    memberId: member.id,
    serviceDate: shape.date,
    chargedCents: shape.charged,
    quantity: shape.quantity,
    itemCode,
    otherPlanPaidCents: Math.floor(shape.charged * shape.otherRatio),
  };
}

const claimArb = fc.record({
  line: lineArb,
  status: fc.constantFrom<ClaimStatus>("draft", "submitted", "paid", "partially_paid", "rejected"),
  paidRatio: fc.double({ min: 0, max: 1, noNaN: true }),
});

function toClaims(plan: Plan, specs: Array<{ line: LineShape; status: ClaimStatus; paidRatio: number }>): Claim[] {
  return specs.map(({ line, status, paidRatio }) => {
    const input = toInput(plan, line);
    const decided = status === "paid" || status === "partially_paid" || status === "rejected";
    const paid = decided ? (status === "rejected" ? 0 : Math.floor(input.chargedCents * paidRatio)) : undefined;
    return makeClaim(
      plan.id,
      input.memberId,
      status,
      [{ benefitId: input.benefitId, date: input.serviceDate, charged: input.chargedCents, quantity: input.quantity, itemCode: input.itemCode, other: input.otherPlanPaidCents }],
      paid,
    );
  });
}

const RUNS = { numRuns: 150 };

describe("estimate invariants", () => {
  it("never pays more than the charge, the balance after another plan, or a negative amount", () => {
    fc.assert(
      fc.property(planArb, fc.array(claimArb, { maxLength: 10 }), lineArb, fc.boolean(), (plan, specs, shape, includeSubmitted) => {
        const claims = toClaims(plan, specs);
        const input = toInput(plan, shape);
        const r = estimateReimbursement(plan, claims, input, { today: TODAY, includeSubmitted });
        expect(r.planPaysCents).toBeGreaterThanOrEqual(0);
        expect(Number.isInteger(r.planPaysCents)).toBe(true);
        expect(r.planPaysCents).toBeLessThanOrEqual(input.chargedCents);
        expect(r.planPaysCents).toBeLessThanOrEqual(input.chargedCents - input.otherPlanPaidCents);
        expect(r.memberPaysCents).toBe(input.chargedCents - input.otherPlanPaidCents - r.planPaysCents);
        for (let i = 1; i < r.steps.length; i++) expect(r.steps[i].planPaysCents).toBeLessThanOrEqual(r.steps[i - 1].planPaysCents);
      }),
      RUNS,
    );
  });

  it("never exceeds the remaining benefit limit or pool, and remaining after is never negative", () => {
    fc.assert(
      fc.property(planArb, fc.array(claimArb, { maxLength: 12 }), lineArb, fc.boolean(), (plan, specs, shape, includeSubmitted) => {
        const claims = toClaims(plan, specs);
        const input = toInput(plan, shape);
        const opts = { today: TODAY, includeSubmitted };
        const r = estimateReimbursement(plan, claims, input, opts);
        const before = benefitUsage(plan, claims, input.benefitId, input.memberId, { ...opts, asOf: input.serviceDate });
        for (const limit of before.limits) {
          if (limit.limit.unit === "cents") expect(r.planPaysCents).toBeLessThanOrEqual(limit.remaining);
          else if (limit.remaining <= 0) expect(r.planPaysCents).toBe(0);
        }
        if (before.pool?.limit.unit === "cents") expect(r.planPaysCents).toBeLessThanOrEqual(before.pool.remaining);
        if (before.frequency && before.frequency.used >= before.frequency.count) expect(r.planPaysCents).toBe(0);
        if (before.inWaitingPeriod && input.serviceDate < (before.waitingPeriodEnds ?? "")) expect(r.planPaysCents).toBe(0);
        if (r.remainingAfter.limitCents != null) expect(r.remainingAfter.limitCents).toBeGreaterThanOrEqual(0);
        if (r.remainingAfter.poolCents != null) expect(r.remainingAfter.poolCents).toBeGreaterThanOrEqual(0);
      }),
      RUNS,
    );
  });
});

describe("usage invariants", () => {
  it("never reports negative remaining amounts", () => {
    fc.assert(
      fc.property(planArb, fc.array(claimArb, { maxLength: 15 }), fc.boolean(), (plan, specs, includeSubmitted) => {
        const usage = computeUsage(plan, toClaims(plan, specs), { today: TODAY, includeSubmitted });
        for (const member of usage.members) {
          for (const b of member.benefits) {
            for (const l of b.limits) expect(l.remaining).toBeGreaterThanOrEqual(0);
            if (b.remainingCents != null) expect(b.remainingCents).toBeGreaterThanOrEqual(0);
            expect(b.usedCents).toBeGreaterThanOrEqual(0);
          }
          for (const p of member.pools) expect(p.remaining).toBeGreaterThanOrEqual(0);
          for (const cs of member.costShares) expect(cs.remainingCents).toBeGreaterThanOrEqual(0);
        }
        for (const cs of usage.family.costShares) expect(cs.remainingCents).toBeGreaterThanOrEqual(0);
      }),
      RUNS,
    );
  });

  it("is monotonic: adding a paid claim never lowers usage or raises what's left", () => {
    const paidArb = fc.record({ line: lineArb, status: fc.constantFrom<ClaimStatus>("paid", "partially_paid"), paidRatio: fc.double({ min: 0, max: 1, noNaN: true }) });
    fc.assert(
      fc.property(planArb, fc.array(paidArb, { maxLength: 10 }), paidArb, (plan, specs, extra) => {
        const base = toClaims(plan, specs);
        const more = [...base, ...toClaims(plan, [extra])];
        const opts = { today: TODAY, includeSubmitted: false };
        const a = computeUsage(plan, base, opts);
        const b = computeUsage(plan, more, opts);
        a.members.forEach((ma, m) => {
          const mb = b.members[m];
          ma.benefits.forEach((ba, i) => {
            const bb = mb.benefits[i];
            expect(bb.usedCents).toBeGreaterThanOrEqual(ba.usedCents);
            ba.limits.forEach((la, j) => {
              expect(bb.limits[j].used).toBeGreaterThanOrEqual(la.used);
              expect(bb.limits[j].remaining).toBeLessThanOrEqual(la.remaining);
            });
            if (ba.frequency && bb.frequency) expect(bb.frequency.used).toBeGreaterThanOrEqual(ba.frequency.used);
          });
          ma.pools.forEach((pa, j) => expect(mb.pools[j].remaining).toBeLessThanOrEqual(pa.remaining));
          ma.costShares.forEach((ca, j) => expect(mb.costShares[j].metCents).toBeGreaterThanOrEqual(ca.metCents));
        });
        a.family.costShares.forEach((ca, j) => expect(b.family.costShares[j].metCents).toBeGreaterThanOrEqual(ca.metCents));
      }),
      RUNS,
    );
  });

  it("keeps the ledger's paid amounts equal to recorded outcomes", () => {
    fc.assert(
      fc.property(planArb, fc.array(claimArb, { maxLength: 12 }), (plan, specs) => {
        const claims = toClaims(plan, specs);
        const ledger = buildLedger(plan, claims, { today: TODAY, includeSubmitted: false });
        for (const claim of claims.filter((c) => c.status === "paid" || c.status === "partially_paid")) {
          const total = ledger.filter((e) => e.claimId === claim.id).reduce((s, e) => s + e.planPaidCents, 0);
          expect(total).toBe(claim.outcome?.paidCents);
        }
        for (const entry of ledger) expect(entry.planPaidCents).toBeGreaterThanOrEqual(0);
      }),
      { numRuns: 60 },
    );
  });
});

describe("period invariants", () => {
  const periodArb: fc.Arbitrary<Period> = fc.oneof(
    fc.record({ kind: fc.constant("benefit_year" as const), startMonth: fc.option(fc.integer({ min: 1, max: 12 }), { nil: null }), startDay: fc.option(fc.integer({ min: 1, max: 31 }), { nil: null }), months: fc.constant(null), years: fc.constant(null) }),
    fc.record({ kind: fc.constant("policy_anniversary" as const), startMonth: fc.constant(null), startDay: fc.constant(null), months: fc.constant(null), years: fc.constant(null) }),
    fc.record({ kind: fc.constant("rolling_months" as const), startMonth: fc.constant(null), startDay: fc.constant(null), months: fc.integer({ min: 1, max: 120 }), years: fc.constant(null) }),
    fc.record({ kind: fc.constant("consecutive_benefit_years" as const), startMonth: fc.option(fc.integer({ min: 1, max: 12 }), { nil: null }), startDay: fc.constant(1), months: fc.constant(null), years: fc.integer({ min: 1, max: 10 }) }),
    fc.record({ kind: fc.constantFrom("lifetime" as const, "per_visit" as const, "per_admission" as const), startMonth: fc.constant(null), startDay: fc.constant(null), months: fc.constant(null), years: fc.constant(null) }),
  );
  const wideDate = fc.integer({ min: 0, max: 365 * 30 }).map((n) => addDays("2010-01-01", n));

  it("always returns a window that contains the date", () => {
    fc.assert(
      fc.property(periodArb, wideDate, planArb, (period, date, plan) => {
        const w = periodWindow(period, date, plan);
        if (!w) return;
        expect(w.start <= w.end).toBe(true);
        expect(inRange(date, w)).toBe(true);
        if (period.kind === "benefit_year") expect([364, 365]).toContain(daysBetween(w.start, w.end));
      }),
      { numRuns: 400 },
    );
  });

  it("never puts a claim deadline before the service date", () => {
    fc.assert(
      fc.property(
        planArb,
        wideDate,
        fc.option(fc.integer({ min: 1, max: 1095 }), { nil: null }),
        fc.option(fc.integer({ min: 0, max: 730 }), { nil: null }),
        (plan, date, submissionDays, daysAfterPeriodEnd) => {
          const deadline = claimDeadline({ ...plan, claimRules: { ...plan.claimRules, submissionDays, daysAfterPeriodEnd } }, date);
          if (submissionDays == null && daysAfterPeriodEnd == null) expect(deadline).toBeNull();
          else expect(deadline! >= date).toBe(true);
        },
      ),
      { numRuns: 400 },
    );
  });

  it("round-trips day arithmetic and keeps month arithmetic in the target month", () => {
    fc.assert(
      fc.property(wideDate, fc.integer({ min: -5000, max: 5000 }), fc.integer({ min: -240, max: 240 }), (date, days, months) => {
        expect(addDays(addDays(date, days), -days)).toBe(date);
        expect(daysBetween(date, addDays(date, days))).toBe(days);
        const shifted = addMonths(date, months);
        const expectedMonth = (((Number(date.slice(5, 7)) - 1 + months) % 12) + 12) % 12 + 1;
        expect(Number(shifted.slice(5, 7))).toBe(expectedMonth);
      }),
      { numRuns: 400 },
    );
  });
});
