// Usage, remaining limits and estimates are all sums over ledger entries inside a period window, so they
// can never disagree with each other.
import { addDays, addMonths, currentBenefitPeriod, inRange, LIFETIME, periodWindow, type DateRange } from "./periods";
import type { FrequencyUsage, LimitUsage, PoolUsage, CostShareUsage, UsageOptions } from "./usage";
import type { Benefit, Category, Claim, CostShare, Frequency, IsoDate, Limit, LimitPool, Plan } from "./types";

type Scope = Limit["scope"];

export interface LedgerEntry {
  claimId: string;
  lineId: string;
  memberId: string;
  benefitId: string;
  serviceDate: IsoDate;
  quantity: number;
  planPaidCents: number;
  /** Member-paid cents toward each deductible or excess, by cost share id. */
  costShareCents: Record<string, number>;
}

export interface PlanIndex {
  benefits: Map<string, Benefit>;
  categories: Map<string, Category>;
  poolByBenefit: Map<string, LimitPool>;
  costSharesByBenefit: Map<string, CostShare[]>;
}

const indexCache = new WeakMap<Plan, PlanIndex>();

export function indexPlan(plan: Plan): PlanIndex {
  const cached = indexCache.get(plan);
  if (cached) return cached;
  const benefits = new Map<string, Benefit>();
  const categories = new Map<string, Category>();
  for (const category of plan.categories) {
    categories.set(category.id, category);
    for (const benefit of category.benefits) benefits.set(benefit.id, benefit);
  }
  const poolByBenefit = new Map<string, LimitPool>();
  for (const pool of plan.limitPools) {
    for (const id of pool.benefitIds) if (!poolByBenefit.has(id)) poolByBenefit.set(id, pool);
  }
  for (const benefit of benefits.values()) {
    const pool = benefit.poolId ? plan.limitPools.find((p) => p.id === benefit.poolId) : undefined;
    if (pool) poolByBenefit.set(benefit.id, pool);
  }
  const costSharesByBenefit = new Map<string, CostShare[]>();
  for (const benefit of benefits.values()) {
    const applicable = plan.costShares.filter(
      (cs) =>
        benefit.costShareIds.includes(cs.id) ||
        cs.appliesToBenefitIds.includes(benefit.id) ||
        cs.appliesToCategoryIds.includes(benefit.categoryId),
    );
    costSharesByBenefit.set(benefit.id, applicable);
  }
  const index = { benefits, categories, poolByBenefit, costSharesByBenefit };
  indexCache.set(plan, index);
  return index;
}

export function allBenefits(plan: Plan): Benefit[] {
  return plan.categories.flatMap((c) => c.benefits);
}

export function countedClaims(claims: Claim[], opts: Pick<UsageOptions, "includeSubmitted">): Claim[] {
  return claims.filter(
    (c) => c.status === "paid" || c.status === "partially_paid" || (opts.includeSubmitted && c.status === "submitted"),
  );
}

function inScope(entry: LedgerEntry, memberId: string | null, scope: Scope): boolean {
  return scope !== "per_person" || memberId === null || entry.memberId === memberId;
}

// Count limits only count services the plan actually paid for.
function measure(entry: LedgerEntry, unit: Limit["unit"]): number {
  if (unit === "cents") return entry.planPaidCents;
  return entry.planPaidCents > 0 ? entry.quantity : 0;
}

function sumEntries(
  ledger: LedgerEntry[],
  benefitIds: ReadonlySet<string>,
  memberId: string | null,
  scope: Scope,
  window: DateRange,
  unit: Limit["unit"],
): number {
  let total = 0;
  for (const entry of ledger) {
    if (benefitIds.has(entry.benefitId) && inScope(entry, memberId, scope) && inRange(entry.serviceDate, window)) {
      total += measure(entry, unit);
    }
  }
  return total;
}

export function limitUsageAt(plan: Plan, ledger: LedgerEntry[], benefitId: string, memberId: string, limit: Limit, asOf: IsoDate): LimitUsage {
  const window = periodWindow(limit.period, asOf, plan);
  if (!window) return { limit, window: null, used: 0, remaining: limit.value };
  const used = sumEntries(ledger, new Set([benefitId]), memberId, limit.scope, window, limit.unit);
  return { limit, window, used, remaining: Math.max(0, limit.value - used) };
}

export function poolUsageAt(plan: Plan, ledger: LedgerEntry[], pool: LimitPool, memberId: string, asOf: IsoDate): PoolUsage {
  const base = { poolId: pool.id, name: pool.name, limit: pool.limit, benefitIds: pool.benefitIds };
  const window = periodWindow(pool.limit.period, asOf, plan);
  if (!window) return { ...base, window: null, used: 0, remaining: pool.limit.value };
  const members = new Set(pool.benefitIds);
  for (const benefit of allBenefits(plan)) if (benefit.poolId === pool.id) members.add(benefit.id);
  const used = sumEntries(ledger, members, memberId, pool.limit.scope, window, pool.limit.unit);
  return { ...base, benefitIds: [...members], window, used, remaining: Math.max(0, pool.limit.value - used) };
}

export function frequencyUsageAt(
  plan: Plan,
  ledger: LedgerEntry[],
  benefitId: string,
  memberId: string,
  frequency: Frequency,
  asOf: IsoDate,
): FrequencyUsage {
  const window = periodWindow(frequency.period, asOf, plan);
  if (!window) return { count: frequency.count, used: 0, window: null, nextEligible: null };
  const dates: IsoDate[] = [];
  for (const entry of ledger) {
    if (entry.benefitId !== benefitId || entry.planPaidCents <= 0) continue;
    if (!inScope(entry, memberId, frequency.scope) || !inRange(entry.serviceDate, window)) continue;
    for (let i = 0; i < Math.max(1, Math.ceil(entry.quantity)); i++) dates.push(entry.serviceDate);
  }
  const used = dates.length;
  if (used < frequency.count) return { count: frequency.count, used, window, nextEligible: null };
  let nextEligible: IsoDate;
  if (frequency.period.kind === "rolling_months") {
    dates.sort();
    // The oldest service that must age out before one more fits in the window.
    const blocking = dates[used - frequency.count];
    nextEligible = addMonths(blocking, frequency.period.months ?? 12);
    while (inRange(blocking, periodWindow(frequency.period, nextEligible, plan) ?? LIFETIME)) nextEligible = addDays(nextEligible, 1);
  } else if (window === LIFETIME) {
    nextEligible = LIFETIME.end;
  } else {
    nextEligible = addDays(window.end, 1);
  }
  return { count: frequency.count, used, window, nextEligible };
}

export function costShareWindow(plan: Plan, costShare: CostShare, asOf: IsoDate): DateRange | null {
  if (!costShare.period) return currentBenefitPeriod(plan, asOf);
  return periodWindow(costShare.period, asOf, plan);
}

export function costShareUsageAt(
  plan: Plan,
  ledger: LedgerEntry[],
  costShare: CostShare,
  memberId: string | null,
  asOf: IsoDate,
): CostShareUsage {
  const amount = costShare.amountCents ?? 0;
  const window = costShareWindow(plan, costShare, asOf);
  const owner = costShare.scope === "per_person" ? memberId : null;
  if (!window) return { costShareId: costShare.id, memberId: owner, window: null, metCents: 0, remainingCents: amount };
  let met = 0;
  for (const entry of ledger) {
    const applied = entry.costShareCents[costShare.id];
    if (applied && inScope(entry, owner, costShare.scope) && inRange(entry.serviceDate, window)) met += applied;
  }
  return { costShareId: costShare.id, memberId: owner, window, metCents: Math.min(met, amount), remainingCents: Math.max(0, amount - met) };
}

export function paidInWindow(ledger: LedgerEntry[], benefitId: string, memberId: string, window: DateRange): number {
  return sumEntries(ledger, new Set([benefitId]), memberId, "per_person", window, "cents");
}
