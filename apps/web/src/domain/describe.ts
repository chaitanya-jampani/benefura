import { formatDate, formatMoney } from "@/lib/format";

import type { Benefit, ClaimStatus, Coverage, Currency, Frequency, IsoDate, Limit, Period } from "./types";

export function money(cents: number, currency: Currency): string {
  return formatMoney(cents, currency, { whole: Math.abs(Math.round(cents)) % 100 === 0 });
}

export function date(iso: IsoDate, currency: Currency): string {
  return formatDate(iso, currency);
}

export function percentText(percent: number): string {
  return `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`;
}

export function periodText(period: Period): string {
  switch (period.kind) {
    case "benefit_year":
      return "a benefit year";
    case "policy_anniversary":
      return "a policy year";
    case "rolling_months":
      return `every ${period.months ?? 12} months`;
    case "consecutive_benefit_years":
      return `in any ${period.years ?? 1} consecutive benefit years`;
    case "lifetime":
      return "for life";
    case "per_visit":
      return "a visit";
    case "per_admission":
      return "an admission";
  }
}

export function currentPeriodText(period: Period): string {
  switch (period.kind) {
    case "benefit_year":
      return "this benefit year";
    case "policy_anniversary":
      return "this policy year";
    case "rolling_months":
      return `in the last ${period.months ?? 12} months`;
    case "consecutive_benefit_years":
      return `across these ${period.years ?? 1} benefit years`;
    case "lifetime":
      return "for life";
    case "per_visit":
      return "this visit";
    case "per_admission":
      return "this admission";
  }
}

const UNIT: Record<Exclude<Limit["unit"], "cents">, [string, string]> = {
  visits: ["visit", "visits"],
  items: ["item", "items"],
  days: ["day", "days"],
  hours: ["hour", "hours"],
};

export function countText(value: number, unit: Exclude<Limit["unit"], "cents">): string {
  const [one, many] = UNIT[unit];
  return `${value} ${value === 1 ? one : many}`;
}

export function limitText(limit: Limit, currency: Currency): string {
  const amount = limit.unit === "cents" ? money(limit.value, currency) : countText(limit.value, limit.unit);
  const family = limit.scope === "per_person" ? "" : " for the family";
  return `${amount} ${periodText(limit.period)}${family}`;
}

export function frequencyText(frequency: Frequency): string {
  return `${frequency.count} ${periodText(frequency.period)}`;
}

export function coverageText(coverage: Coverage, currency: Currency): string {
  switch (coverage.kind) {
    case "percent":
      return percentText(coverage.percent ?? 0);
    case "percent_capped":
      return `${percentText(coverage.percent ?? 0)} up to ${money(coverage.capCents ?? 0, currency)} a visit`;
    case "fixed_per_service":
      return `${money(coverage.amountCents ?? 0, currency)} a service`;
    case "per_diem":
      return `${money(coverage.amountCents ?? 0, currency)} a day`;
    case "schedule": {
      if (coverage.scheduleItems.length === 0) return percentText(coverage.percent ?? 0);
      const amounts = coverage.scheduleItems.map((i) => i.benefitCents);
      const low = Math.min(...amounts);
      const high = Math.max(...amounts);
      return low === high
        ? `${money(low, currency)} per item`
        : `${money(low, currency)} to ${money(high, currency)} per item`;
    }
  }
}

export function benefitRuleText(benefit: Benefit, currency: Currency): string {
  const parts = [coverageText(benefit.coverage, currency)];
  const money = benefit.limits.find((l) => l.unit === "cents");
  if (money) parts.push(limitText(money, currency));
  if (benefit.frequency) parts.push(frequencyText(benefit.frequency));
  return parts.join(", ");
}

export const STATUS_LABEL: Record<ClaimStatus, string> = {
  draft: "Draft",
  submitted: "Submitted",
  paid: "Paid",
  partially_paid: "Partially paid",
  rejected: "Rejected",
};
