import { countText, coverageText, date, frequencyText, money } from "@/domain/describe";
import { indexPlan } from "@/domain/ledger";
import { addDays, currentBenefitPeriod, daysBetween } from "@/domain/periods";
import type { BenefitUsage, MemberUsage } from "@/domain/usage";
import type { Benefit, Category, Claim, IsoDate, Limit, Plan } from "@/domain/types";

export type Tone = "ink" | "muted" | "positive" | "caution" | "negative";

export interface MeterModel {
  used: number;
  max: number;
  label: string;
}

export interface BenefitRow {
  benefit: Benefit;
  category: Category;
  usage: BenefitUsage;
  inUse: boolean;
  subtitle: string;
  meter: MeterModel | null;
  value: string;
  caption: string;
  tone: Tone;
}

export function coverageSummary(plan: Plan, benefit: Benefit): string {
  const base = coverageText(benefit.coverage, plan.currency);
  const share = indexPlan(plan)
    .costSharesByBenefit.get(benefit.id)
    ?.find((cs) => (cs.kind === "deductible" || cs.kind === "excess") && (cs.amountCents ?? 0) > 0);
  if (!share) return base;
  const family = share.scope === "per_person" ? "" : "family ";
  return `${base} after the ${money(share.amountCents ?? 0, plan.currency)} ${family}${share.kind}`;
}

export function benefitRow(plan: Plan, usage: BenefitUsage): BenefitRow {
  const index = indexPlan(plan);
  const benefit = index.benefits.get(usage.benefitId)!;
  const category = index.categories.get(benefit.categoryId)!;
  const c = plan.currency;
  const moneyLimit = usage.limits.find((l) => l.limit.unit === "cents" && l.window);
  const perVisit = usage.limits.find((l) => l.limit.unit === "cents" && !l.window);
  const countLimit = usage.limits.find((l) => l.limit.unit !== "cents" && l.window);
  const pool = usage.pool && usage.pool.limit.unit === "cents" && usage.pool.window ? usage.pool : null;
  const inUse =
    usage.usedCents > 0 || usage.limits.some((l) => l.used > 0) || (usage.frequency?.used ?? 0) > 0;

  let subtitle = coverageSummary(plan, benefit);
  if (usage.inWaitingPeriod && usage.waitingPeriodEnds) subtitle = `Covered from ${date(usage.waitingPeriodEnds, c)}`;
  else if (usage.frequency?.nextEligible) subtitle = `Next eligible ${date(usage.frequency.nextEligible, c)}`;

  let meter: MeterModel | null = null;
  let value = "No maximum";
  let caption = "";
  let tone: Tone = "muted";

  if (moneyLimit) {
    const remaining = usage.remainingCents ?? moneyLimit.remaining;
    meter = { used: moneyLimit.used, max: moneyLimit.limit.value, label: `${benefit.name}: ${money(moneyLimit.used, c)} used of ${money(moneyLimit.limit.value, c)}` };
    value = money(remaining, c);
    caption = pool && pool.remaining < moneyLimit.remaining ? "left, shared maximum" : `left of ${money(moneyLimit.limit.value, c)}`;
    tone = remaining === 0 ? "negative" : "ink";
  } else if (countLimit) {
    meter = { used: countLimit.used, max: countLimit.limit.value, label: `${benefit.name}: ${countLimit.used} of ${countLimit.limit.value} used` };
    value = `${countLimit.remaining} left`;
    caption = `of ${countText(countLimit.limit.value, countLimit.limit.unit as Exclude<Limit["unit"], "cents">)}`;
    tone = countLimit.remaining === 0 ? "negative" : "ink";
  } else if (pool) {
    meter = { used: pool.used, max: pool.limit.value, label: `${pool.name}: ${money(pool.used, c)} used of ${money(pool.limit.value, c)}` };
    value = money(pool.remaining, c);
    caption = `left of ${money(pool.limit.value, c)} shared`;
    tone = pool.remaining === 0 ? "negative" : "ink";
  } else if (perVisit) {
    value = money(perVisit.limit.value, c);
    caption = perVisit.limit.period.kind === "per_admission" ? "an admission" : "a visit";
    tone = "ink";
  } else if (benefit.frequency) {
    value = usage.frequency?.nextEligible ? "Not yet" : "Eligible";
    caption = frequencyText(benefit.frequency);
    tone = usage.frequency?.nextEligible ? "caution" : "positive";
  }

  if (usage.inWaitingPeriod) {
    value = "Waiting";
    caption = usage.waitingPeriodEnds ? `until ${date(usage.waitingPeriodEnds, c)}` : "";
    tone = "caution";
  }

  return { benefit, category, usage, inUse, subtitle, meter, value, caption, tone };
}

export function benefitRows(plan: Plan, member: MemberUsage): BenefitRow[] {
  return member.benefits.map((u) => benefitRow(plan, u));
}

export interface DeadlineItem {
  claim: Claim;
  deadline: IsoDate;
  daysLeft: number;
  tone: Tone;
}

export function draftDeadlines(claims: Claim[], today: IsoDate): DeadlineItem[] {
  return claims
    .filter((c): c is Claim & { deadline: string } => c.status === "draft" && !!c.deadline)
    .map((claim) => {
      const daysLeft = daysBetween(today, claim.deadline);
      return { claim, deadline: claim.deadline, daysLeft, tone: (daysLeft < 0 ? "negative" : daysLeft <= 30 ? "caution" : "muted") as Tone };
    })
    .sort((a, b) => a.deadline.localeCompare(b.deadline));
}

export function daysLeftText(daysLeft: number): string {
  if (daysLeft < 0) return "Deadline passed";
  if (daysLeft === 0) return "Due today";
  if (daysLeft === 1) return "1 day left";
  return `${daysLeft} days left`;
}

export function deadlineRuleText(plan: Plan, today: IsoDate): string | null {
  const rules = plan.claimRules;
  const c = plan.currency;
  if (rules.daysAfterPeriodEnd != null) {
    const year = currentBenefitPeriod(plan, today);
    return `Claims for services this benefit year are due by ${date(addDays(year.end, rules.daysAfterPeriodEnd), c)}.`;
  }
  if (rules.submissionDays != null) {
    const d = rules.submissionDays;
    const span = d % 365 === 0 ? `${d / 365} year${d === 365 ? "" : "s"}` : `${d} days`;
    return `Claims are due within ${span} of the service date.`;
  }
  return null;
}

export function outOfPocketThisYear(plan: Plan, claims: Claim[], memberId: string, today: IsoDate): number {
  const year = currentBenefitPeriod(plan, today);
  return claims
    .filter((c) => c.planId === plan.id && c.patientMemberId === memberId && (c.status === "paid" || c.status === "partially_paid" || c.status === "rejected"))
    .filter((c) => c.lines.some((l) => l.serviceDate >= year.start && l.serviceDate <= year.end))
    .reduce((sum, c) => {
      const charged = c.lines.reduce((s, l) => s + l.chargedCents - (l.otherPlanPaidCents ?? 0), 0);
      return sum + Math.max(0, charged - (c.outcome?.paidCents ?? 0));
    }, 0);
}

export const RELATIONSHIP_LABEL = { self: "You", spouse: "Spouse", child: "Child", dependent: "Dependent" } as const;
