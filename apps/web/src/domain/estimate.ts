// Steps run in a fixed order and each can only lower the running plan payment: coverage → per-service
// cap or schedule → other plan → deductible → frequency → limit → pool → waiting period.
// Percentages and proportional count-limit cuts (1 of 2 visits left) both round half up to the cent.
import { countText, currentPeriodText, date, frequencyText, limitText, money, percentText } from "./describe";
import {
  costShareUsageAt,
  countedClaims,
  frequencyUsageAt,
  indexPlan,
  limitUsageAt,
  poolUsageAt,
  type LedgerEntry,
} from "./ledger";
import { allocate, clampCents, percentOf, roundHalfUp } from "./money";
import { claimDeadline, waitingPeriodEnds } from "./periods";
import type { UsageOptions } from "./usage";
import type { Claim, ClaimLine, Coverage, Currency, IsoDate, Plan } from "./types";

export interface EstimateInput {
  benefitId: string;
  memberId: string;
  serviceDate: IsoDate;
  chargedCents: number;
  quantity?: number;
  itemCode?: string | null;
  otherPlanPaidCents?: number;
}

export type EstimateLimiter =
  | "coverage"
  | "per_service_cap"
  | "schedule"
  | "other_plan"
  | "limit"
  | "pool"
  | "frequency"
  | "waiting_period"
  | "deductible"
  | "not_covered"
  | null;

export interface EstimateStep {
  label: string;
  /** Running total after this step. */
  planPaysCents: number;
  detail?: string;
}

export interface EstimateResult {
  benefitId: string;
  eligibleCents: number;
  planPaysCents: number;
  memberPaysCents: number;
  limitedBy: EstimateLimiter;
  steps: EstimateStep[];
  warnings: string[];
  remainingAfter: { limitCents: number | null; poolCents: number | null };
  deadline: IsoDate | null;
}

interface LedgerEstimate {
  result: EstimateResult;
  quantity: number;
  costShareCents: Record<string, number>;
}

function normalizeCode(code: string | null | undefined): string {
  return (code ?? "").trim().replace(/^item\s*/i, "").toLowerCase();
}

interface CoverageAmount {
  cents: number;
  capped: boolean;
  scheduleItem: { itemCode: string; description: string; benefitCents: number } | null;
}

function coverageAmount(coverage: Coverage, chargedCents: number, quantity: number, itemCode: string | null | undefined): CoverageAmount {
  const charged = Math.max(0, chargedCents);
  switch (coverage.kind) {
    case "percent":
      return { cents: percentOf(charged, coverage.percent ?? 0), capped: false, scheduleItem: null };
    case "percent_capped": {
      const raw = percentOf(charged, coverage.percent ?? 0);
      const cap = coverage.capCents == null ? Infinity : roundHalfUp(coverage.capCents * quantity);
      return { cents: Math.min(raw, cap), capped: raw > cap, scheduleItem: null };
    }
    case "fixed_per_service":
    case "per_diem":
      return { cents: Math.min(charged, roundHalfUp((coverage.amountCents ?? 0) * quantity)), capped: false, scheduleItem: null };
    case "schedule": {
      const code = normalizeCode(itemCode);
      const item = code ? coverage.scheduleItems.find((i) => normalizeCode(i.itemCode) === code) ?? null : null;
      if (item) return { cents: Math.min(charged, roundHalfUp(item.benefitCents * quantity)), capped: false, scheduleItem: item };
      if (coverage.percent != null) return { cents: percentOf(charged, coverage.percent), capped: false, scheduleItem: null };
      return { cents: 0, capped: false, scheduleItem: null };
    }
  }
}

function emptyResult(input: EstimateInput, deadline: IsoDate | null, label: string, warning: string): EstimateResult {
  const charged = clampCents(input.chargedCents);
  const other = Math.min(charged, clampCents(input.otherPlanPaidCents ?? 0));
  return {
    benefitId: input.benefitId,
    eligibleCents: charged,
    planPaysCents: 0,
    memberPaysCents: charged - other,
    limitedBy: "not_covered",
    steps: [{ label, planPaysCents: 0 }],
    warnings: [warning],
    remainingAfter: { limitCents: null, poolCents: null },
    deadline,
  };
}

export function estimateOnLedger(plan: Plan, ledger: LedgerEntry[], input: EstimateInput, today: IsoDate): LedgerEstimate {
  const currency: Currency = plan.currency;
  const index = indexPlan(plan);
  const benefit = index.benefits.get(input.benefitId);
  const quantity = input.quantity != null && input.quantity > 0 ? input.quantity : 1;
  const deadline = claimDeadline(plan, input.serviceDate);
  if (!benefit) {
    return {
      result: emptyResult(input, deadline, "Not a benefit in this plan", "Pick a benefit from your plan to estimate."),
      quantity,
      costShareCents: {},
    };
  }
  if (plan.effectiveDate && input.serviceDate < plan.effectiveDate) {
    return {
      result: emptyResult(
        input,
        deadline,
        "Before your coverage started",
        `Coverage starts on ${date(plan.effectiveDate, currency)}.`,
      ),
      quantity,
      costShareCents: {},
    };
  }

  const charged = clampCents(input.chargedCents);
  const other = Math.min(charged, clampCents(input.otherPlanPaidCents ?? 0));
  const balance = charged - other;
  const coverage = benefit.coverage;
  const steps: EstimateStep[] = [];
  const warnings: string[] = [];
  const costShareCents: Record<string, number> = {};
  let limitedBy: EstimateLimiter = null;
  let pays = 0;

  const lower = (next: number, reason: Exclude<EstimateLimiter, null>) => {
    if (next < pays) {
      pays = Math.max(0, next);
      limitedBy = reason;
    }
  };

  const covered = coverageAmount(coverage, charged, quantity, input.itemCode);
  switch (coverage.kind) {
    case "percent":
    case "percent_capped":
      pays = percentOf(charged, coverage.percent ?? 0);
      steps.push({ label: `${percentText(coverage.percent ?? 0)} of ${money(charged, currency)}`, planPaysCents: pays });
      break;
    case "fixed_per_service":
    case "per_diem": {
      pays = roundHalfUp((coverage.amountCents ?? 0) * quantity);
      const per = coverage.kind === "per_diem" ? "a day" : "a service";
      const times = quantity === 1 ? "" : ` for ${quantity} ${coverage.kind === "per_diem" ? "days" : "services"}`;
      steps.push({ label: `${money(coverage.amountCents ?? 0, currency)} ${per}${times}`, planPaysCents: pays });
      break;
    }
    case "schedule":
      if (covered.scheduleItem) {
        pays = roundHalfUp(covered.scheduleItem.benefitCents * quantity);
        steps.push({
          label: `Item ${covered.scheduleItem.itemCode} pays ${money(covered.scheduleItem.benefitCents, currency)}${quantity === 1 ? "" : ` × ${quantity}`}`,
          planPaysCents: pays,
          detail: covered.scheduleItem.description,
        });
      } else if (coverage.percent != null) {
        pays = percentOf(charged, coverage.percent);
        steps.push({
          label: `${percentText(coverage.percent)} of ${money(charged, currency)}`,
          planPaysCents: pays,
          detail: input.itemCode ? `Item ${input.itemCode} isn't on the schedule, so the fallback rate applies` : undefined,
        });
      } else {
        pays = 0;
        limitedBy = "schedule";
        const codes = coverage.scheduleItems.map((i) => i.itemCode).join(", ");
        steps.push({ label: input.itemCode ? `Item ${input.itemCode} isn't on the schedule` : "No item number", planPaysCents: 0 });
        warnings.push(
          input.itemCode
            ? `Item ${input.itemCode} isn't covered under ${benefit.name}. Covered items: ${codes}.`
            : `${benefit.name} pays a set amount per item number. Add one of: ${codes}.`,
        );
      }
      break;
  }
  if (pays < charged && limitedBy === null) limitedBy = coverage.kind === "schedule" ? "schedule" : "coverage";

  if (coverage.kind === "percent_capped" && covered.capped) {
    lower(covered.cents, "per_service_cap");
    steps.push({ label: `Capped at ${money(coverage.capCents ?? 0, currency)} a visit${quantity === 1 ? "" : ` × ${quantity}`}`, planPaysCents: pays });
  } else if (pays > charged) {
    pays = charged;
    limitedBy = null;
    steps.push({ label: `Up to the ${money(charged, currency)} charged`, planPaysCents: pays });
  }

  if (other > 0) {
    lower(balance, "other_plan");
    steps.push({
      label: `Other plan paid ${money(other, currency)}`,
      planPaysCents: pays,
      detail: `This plan pays at most the ${money(balance, currency)} balance`,
    });
  }

  // The member pays deductibles and excesses first, then coverage applies to what's left.
  for (const share of index.costSharesByBenefit.get(benefit.id) ?? []) {
    if (share.kind === "coinsurance") continue;
    const amount = share.amountCents ?? 0;
    if (amount <= 0) continue;
    if (share.kind === "copay") {
      lower(pays - roundHalfUp(amount * quantity), "deductible");
      steps.push({ label: `${share.name}: ${money(amount, currency)} a service`, planPaysCents: pays });
      continue;
    }
    const usage = costShareUsageAt(plan, ledger, share, input.memberId, input.serviceDate);
    const applied = Math.min(usage.remainingCents, balance);
    costShareCents[share.id] = applied;
    if (usage.remainingCents === 0) {
      steps.push({ label: `${share.name} already met`, planPaysCents: pays });
    } else if (applied > 0) {
      const afterShare = Math.min(coverageAmount(coverage, charged - applied, quantity, input.itemCode).cents, balance - applied);
      lower(afterShare, "deductible");
      steps.push({
        label: `${share.name}: ${money(applied, currency)} paid by you first`,
        planPaysCents: pays,
        detail: `${money(usage.remainingCents, currency)} of the ${money(amount, currency)} ${share.scope === "per_person" ? "" : "family "}${share.kind} was still to meet`,
      });
    }
  }

  if (benefit.frequency) {
    const usage = frequencyUsageAt(plan, ledger, benefit.id, input.memberId, benefit.frequency, input.serviceDate);
    const allowed = benefit.frequency.count - usage.used;
    if (allowed <= 0) {
      lower(0, "frequency");
      steps.push({ label: `Allowed ${frequencyText(benefit.frequency)}, already used`, planPaysCents: 0 });
      if (usage.nextEligible && usage.nextEligible !== "9999-12-31") {
        warnings.push(`Not eligible again until ${date(usage.nextEligible, currency)}.`);
      } else {
        warnings.push(`${benefit.name} has already been used.`);
      }
    } else {
      if (quantity > allowed) lower(roundHalfUp(pays * allowed, quantity), "frequency");
      steps.push({ label: `Allowed ${frequencyText(benefit.frequency)}, ${allowed} available`, planPaysCents: pays });
    }
  }

  const moneyLimits: number[] = [];
  for (const limit of benefit.limits) {
    const usage = limitUsageAt(plan, ledger, benefit.id, input.memberId, limit, input.serviceDate);
    if (limit.unit === "cents") {
      lower(usage.remaining, "limit");
      if (usage.window) moneyLimits.push(usage.remaining);
      steps.push({
        label: usage.window
          ? `${money(usage.remaining, currency)} left of ${money(limit.value, currency)} ${currentPeriodText(limit.period)}`
          : `Up to ${limitText(limit, currency)}`,
        planPaysCents: pays,
      });
    } else {
      if (usage.remaining <= 0) lower(0, "limit");
      else if (quantity > usage.remaining) lower(roundHalfUp(pays * usage.remaining, quantity), "limit");
      steps.push({
        label: `${usage.remaining} left of ${limitText(limit, currency)}`,
        planPaysCents: pays,
      });
    }
    if (usage.remaining <= 0) warnings.push(`The ${limitText(limit, currency)} maximum is used up.`);
  }

  const pool = index.poolByBenefit.get(benefit.id);
  let poolRemaining: number | null = null;
  if (pool) {
    const usage = poolUsageAt(plan, ledger, pool, input.memberId, input.serviceDate);
    if (pool.limit.unit === "cents") {
      lower(usage.remaining, "pool");
      if (usage.window) poolRemaining = usage.remaining;
      steps.push({ label: `${pool.name}: ${money(usage.remaining, currency)} left of ${money(pool.limit.value, currency)}`, planPaysCents: pays });
    } else {
      if (usage.remaining <= 0) lower(0, "pool");
      else if (quantity > usage.remaining) lower(roundHalfUp(pays * usage.remaining, quantity), "pool");
      steps.push({ label: `${pool.name}: ${countText(usage.remaining, pool.limit.unit)} left`, planPaysCents: pays });
    }
    if (usage.remaining <= 0) warnings.push(`The ${pool.name.toLowerCase()} is used up.`);
  }

  const waitEnds = waitingPeriodEnds(benefit, plan);
  if (waitEnds && input.serviceDate < waitEnds) {
    lower(0, "waiting_period");
    pays = 0;
    limitedBy = "waiting_period";
    steps.push({ label: `Waiting period until ${date(waitEnds, currency)}`, planPaysCents: 0 });
    warnings.push(`${benefit.name} is covered for services from ${date(waitEnds, currency)}.`);
  }

  if (deadline && deadline < today) warnings.push(`The deadline to claim this was ${date(deadline, currency)}.`);
  if (index.categories.get(benefit.categoryId)?.kind === "hospital") {
    warnings.push("Hospital estimates are a guide only: agreements, gap fees and clinical categories change what's paid.");
  }

  return {
    result: {
      benefitId: benefit.id,
      eligibleCents: charged,
      planPaysCents: pays,
      memberPaysCents: balance - pays,
      limitedBy: pays === charged && limitedBy !== "waiting_period" ? null : limitedBy,
      steps,
      warnings,
      remainingAfter: {
        limitCents: moneyLimits.length ? Math.max(0, Math.min(...moneyLimits) - pays) : null,
        poolCents: poolRemaining == null ? null : Math.max(0, poolRemaining - pays),
      },
      deadline,
    },
    quantity,
    costShareCents,
  };
}

export interface BuildLedgerOptions extends Pick<UsageOptions, "includeSubmitted" | "today"> {
  excludeClaimId?: string;
}

function firstServiceDate(claim: Claim): IsoDate {
  return claim.lines.reduce((min, l) => (l.serviceDate < min ? l.serviceDate : min), claim.lines[0]?.serviceDate ?? "9999-12-31");
}

/**
 * Replays counted claims in service-date order. Submitted claims count at their estimate; recorded payments
 * are split across lines in proportion to each line's estimate, or its balance when nothing was estimated.
 */
export function buildLedger(plan: Plan, claims: Claim[], opts: BuildLedgerOptions): LedgerEntry[] {
  const index = indexPlan(plan);
  const ordered = countedClaims(
    claims.filter((c) => c.planId === plan.id && c.id !== opts.excludeClaimId),
    opts,
  ).sort(
    (a, b) =>
      firstServiceDate(a).localeCompare(firstServiceDate(b)) || a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id),
  );
  const ledger: LedgerEntry[] = [];
  for (const claim of ordered) {
    const lines = claim.lines
      .filter((l) => index.benefits.has(l.benefitId))
      .sort((a, b) => a.serviceDate.localeCompare(b.serviceDate));
    const entries: LedgerEntry[] = [];
    for (const line of lines) {
      const { result, quantity, costShareCents } = estimateOnLedger(plan, ledger, lineInput(line, claim.patientMemberId), opts.today);
      const entry: LedgerEntry = {
        claimId: claim.id,
        lineId: line.id,
        memberId: claim.patientMemberId,
        benefitId: line.benefitId,
        serviceDate: line.serviceDate,
        quantity,
        planPaidCents: result.planPaysCents,
        costShareCents,
      };
      ledger.push(entry);
      entries.push(entry);
    }
    if ((claim.status === "paid" || claim.status === "partially_paid") && claim.outcome && entries.length > 0) {
      const estimates = entries.map((e) => e.planPaidCents);
      const weights = estimates.some((c) => c > 0)
        ? estimates
        : lines.map((l) => Math.max(0, l.chargedCents - (l.otherPlanPaidCents ?? 0)));
      allocate(clampCents(claim.outcome.paidCents), weights).forEach((cents, i) => {
        entries[i].planPaidCents = cents;
      });
    }
  }
  return ledger;
}

export function lineInput(line: Pick<ClaimLine, "benefitId" | "serviceDate" | "chargedCents" | "quantity" | "itemCode" | "otherPlanPaidCents">, memberId: string): EstimateInput {
  return {
    benefitId: line.benefitId,
    memberId,
    serviceDate: line.serviceDate,
    chargedCents: line.chargedCents,
    quantity: line.quantity,
    itemCode: line.itemCode,
    otherPlanPaidCents: line.otherPlanPaidCents,
  };
}

export function estimateReimbursement(
  plan: Plan,
  claims: Claim[],
  input: EstimateInput,
  opts: UsageOptions & { excludeClaimId?: string },
): EstimateResult {
  const ledger = buildLedger(plan, claims, opts);
  return estimateOnLedger(plan, ledger, input, opts.today).result;
}

export interface ClaimEstimate {
  /** In input order. */
  lines: EstimateResult[];
  chargedCents: number;
  planPaysCents: number;
  memberPaysCents: number;
  deadline: IsoDate | null;
}

/** Lines apply in service-date order, so a second visit on the same claim sees the first one's use of the limit. */
export function estimateClaim(
  plan: Plan,
  claims: Claim[],
  claim: { patientMemberId: string; lines: Array<Omit<ClaimLine, "id"> & { id?: string }> },
  opts: UsageOptions & { excludeClaimId?: string },
): ClaimEstimate {
  const ledger = buildLedger(plan, claims, opts);
  const order = claim.lines.map((line, i) => ({ line, i })).sort((a, b) => a.line.serviceDate.localeCompare(b.line.serviceDate) || a.i - b.i);
  const results: EstimateResult[] = new Array(claim.lines.length);
  for (const { line, i } of order) {
    const { result, quantity, costShareCents } = estimateOnLedger(plan, ledger, lineInput(line, claim.patientMemberId), opts.today);
    results[i] = result;
    ledger.push({
      claimId: "__estimate__",
      lineId: line.id ?? `line-${i}`,
      memberId: claim.patientMemberId,
      benefitId: line.benefitId,
      serviceDate: line.serviceDate,
      quantity,
      planPaidCents: result.planPaysCents,
      costShareCents,
    });
  }
  const deadlines = results.map((r) => r.deadline).filter((d): d is IsoDate => d != null).sort();
  return {
    lines: results,
    chargedCents: results.reduce((s, r) => s + r.eligibleCents, 0),
    planPaysCents: results.reduce((s, r) => s + r.planPaysCents, 0),
    memberPaysCents: results.reduce((s, r) => s + r.memberPaysCents, 0),
    deadline: deadlines[0] ?? null,
  };
}
