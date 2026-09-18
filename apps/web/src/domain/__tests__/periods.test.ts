import { describe, expect, it } from "vitest";

import { AU_DEMO_PLAN as AU, CA_DEMO_PLAN as CA } from "../fixtures";
import {
  addDays,
  addMonths,
  claimDeadline,
  currentBenefitPeriod,
  daysBetween,
  LIFETIME,
  nextResetDate,
  periodWindow,
  waitingPeriodEnds,
} from "../periods";
import type { Benefit, Period, Plan } from "../types";

const period = (p: Partial<Period> & Pick<Period, "kind">): Period => ({
  startMonth: null,
  startDay: null,
  months: null,
  years: null,
  ...p,
});

const benefit = (plan: Plan, id: string): Benefit => plan.categories.flatMap((c) => c.benefits).find((b) => b.id === id)!;

describe("date math", () => {
  it("adds days across month, year and leap boundaries", () => {
    expect(addDays("2024-02-28", 1)).toBe("2024-02-29");
    expect(addDays("2023-12-31", 1)).toBe("2024-01-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(addDays("2026-09-16", 730)).toBe("2028-09-15");
    expect(daysBetween("2026-01-01", "2027-01-01")).toBe(365);
  });

  it("adds months and clamps the day", () => {
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(addMonths("2024-01-31", 1)).toBe("2024-02-29");
    expect(addMonths("2026-03-15", -15)).toBe("2024-12-15");
    expect(addMonths("2026-02-10", 9)).toBe("2026-11-10");
  });
});

describe("period windows", () => {
  it("uses the plan's January benefit year", () => {
    expect(periodWindow(CA.benefitPeriod, "2026-09-16", CA)).toEqual({ start: "2026-01-01", end: "2026-12-31" });
    expect(currentBenefitPeriod(CA, "2026-01-01")).toEqual({ start: "2026-01-01", end: "2026-12-31" });
    expect(nextResetDate(CA.benefitPeriod, CA, "2026-09-16")).toBe("2027-01-01");
  });

  it("handles benefit years that start mid-year", () => {
    const july = period({ kind: "benefit_year", startMonth: 7, startDay: 1 });
    expect(periodWindow(july, "2026-03-01", CA)).toEqual({ start: "2025-07-01", end: "2026-06-30" });
    expect(periodWindow(july, "2026-07-01", CA)).toEqual({ start: "2026-07-01", end: "2027-06-30" });
  });

  it("lets a benefit-year limit inherit the plan's start when it has none", () => {
    const plan = { ...CA, benefitPeriod: period({ kind: "benefit_year", startMonth: 4, startDay: 1 }) };
    expect(periodWindow(period({ kind: "benefit_year" }), "2026-03-31", plan)).toEqual({ start: "2025-04-01", end: "2026-03-31" });
  });

  it("anchors policy years on the effective date", () => {
    const p = period({ kind: "policy_anniversary" });
    expect(periodWindow(p, "2026-06-30", AU)).toEqual({ start: "2025-07-01", end: "2026-06-30" });
    expect(periodWindow(p, "2026-07-01", AU)).toEqual({ start: "2026-07-01", end: "2027-06-30" });
  });

  it("ends rolling windows on the service date", () => {
    const rolling = period({ kind: "rolling_months", months: 24 });
    expect(periodWindow(rolling, "2026-09-16", CA)).toEqual({ start: "2024-09-17", end: "2026-09-16" });
    expect(nextResetDate(rolling, CA, "2026-09-16")).toBeNull();
  });

  it("groups CA eyewear into 2 consecutive benefit years from the first benefit year", () => {
    const eyewear = benefit(CA, "ben-eyewear").limits[0].period;
    expect(periodWindow(eyewear, "2025-03-01", CA)).toEqual({ start: "2025-01-01", end: "2026-12-31" });
    expect(periodWindow(eyewear, "2026-09-16", CA)).toEqual({ start: "2025-01-01", end: "2026-12-31" });
    expect(periodWindow(eyewear, "2027-01-01", CA)).toEqual({ start: "2027-01-01", end: "2028-12-31" });
    expect(nextResetDate(eyewear, CA, "2026-09-16")).toBe("2027-01-01");
  });

  it("returns lifetime and per-visit windows", () => {
    expect(periodWindow(period({ kind: "lifetime" }), "2026-09-16", CA)).toBe(LIFETIME);
    expect(nextResetDate(period({ kind: "lifetime" }), CA, "2026-09-16")).toBeNull();
    expect(periodWindow(period({ kind: "per_visit" }), "2026-09-16", CA)).toBeNull();
    expect(periodWindow(period({ kind: "per_admission" }), "2026-09-16", CA)).toBeNull();
  });
});

describe("deadlines and waiting periods", () => {
  it("gives CA claims until 90 days after the benefit year ends", () => {
    expect(claimDeadline(CA, "2026-03-10")).toBe("2027-03-31");
    expect(claimDeadline(CA, "2026-12-31")).toBe("2027-03-31");
  });

  it("gives AU extras claims 2 years (730 days) from the service", () => {
    expect(claimDeadline(AU, "2026-03-10")).toBe("2028-03-09");
  });

  it("uses the later deadline when both rules are set", () => {
    const short = { ...CA, claimRules: { ...CA.claimRules, submissionDays: 30 } };
    expect(claimDeadline(short, "2026-12-20")).toBe("2027-03-31");
    const long = { ...CA, claimRules: { ...CA.claimRules, submissionDays: 365 } };
    expect(claimDeadline(long, "2026-12-20")).toBe("2027-12-20");
    const none = { ...CA, claimRules: { ...CA.claimRules, daysAfterPeriodEnd: null } };
    expect(claimDeadline(none, "2026-12-20")).toBeNull();
  });

  it("counts waiting periods from the effective date", () => {
    expect(waitingPeriodEnds(benefit(AU, "ben-major-dental"), AU)).toBe("2025-07-01");
    expect(waitingPeriodEnds(benefit(AU, "ben-optical"), AU)).toBe("2025-01-01");
    expect(waitingPeriodEnds(benefit(CA, "ben-dental-major"), CA)).toBe("2026-01-01");
    expect(waitingPeriodEnds(benefit(AU, "ben-ambulance"), AU)).toBeNull();
    expect(waitingPeriodEnds(benefit(CA, "ben-massage"), CA)).toBeNull();
    expect(waitingPeriodEnds(benefit(AU, "ben-major-dental"), AU, "2026-05-01")).toBe("2027-05-01");
  });
});
