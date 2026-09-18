import { buildLedger } from "./estimate";
import {
  allBenefits,
  costShareUsageAt,
  frequencyUsageAt,
  indexPlan,
  limitUsageAt,
  paidInWindow,
  poolUsageAt,
  type LedgerEntry,
} from "./ledger";
import { currentBenefitPeriod, waitingPeriodEnds, type DateRange } from "./periods";
import type { Benefit, Claim, IsoDate, Limit, Plan } from "./types";

export { countedClaims } from "./ledger";

export interface UsageOptions {
  today: IsoDate;
  /** Also count `submitted` claims, at their estimated plan payment. */
  includeSubmitted: boolean;
}

export interface LimitUsage {
  limit: Limit;
  /** Null for per-visit limits. */
  window: DateRange | null;
  used: number;
  remaining: number;
}

export interface PoolUsage {
  poolId: string;
  name: string;
  limit: Limit;
  window: DateRange | null;
  used: number;
  remaining: number;
  benefitIds: string[];
}

export interface FrequencyUsage {
  count: number;
  used: number;
  window: DateRange | null;
  /** Null when eligible today. */
  nextEligible: IsoDate | null;
}

export interface BenefitUsage {
  benefitId: string;
  memberId: string;
  limits: LimitUsage[];
  pool: PoolUsage | null;
  frequency: FrequencyUsage | null;
  /** Smallest remainder across money limits and the pool; null when unlimited. */
  remainingCents: number | null;
  /** In the current benefit period. */
  usedCents: number;
  waitingPeriodEnds: IsoDate | null;
  inWaitingPeriod: boolean;
}

export interface CostShareUsage {
  costShareId: string;
  memberId: string | null;
  window: DateRange | null;
  metCents: number;
  remainingCents: number;
}

export interface MemberUsage {
  memberId: string;
  benefits: BenefitUsage[];
  pools: PoolUsage[];
  costShares: CostShareUsage[];
}

export interface PlanUsage {
  asOf: IsoDate;
  members: MemberUsage[];
  family: { costShares: CostShareUsage[] };
}

function benefitUsageOnLedger(plan: Plan, ledger: LedgerEntry[], benefit: Benefit, memberId: string, asOf: IsoDate): BenefitUsage {
  const limits = benefit.limits.map((limit) => limitUsageAt(plan, ledger, benefit.id, memberId, limit, asOf));
  const poolDef = indexPlan(plan).poolByBenefit.get(benefit.id);
  const pool = poolDef ? poolUsageAt(plan, ledger, poolDef, memberId, asOf) : null;
  const frequency = benefit.frequency ? frequencyUsageAt(plan, ledger, benefit.id, memberId, benefit.frequency, asOf) : null;
  const moneyRemaining = [
    ...limits.filter((l) => l.limit.unit === "cents" && l.window).map((l) => l.remaining),
    ...(pool && pool.limit.unit === "cents" && pool.window ? [pool.remaining] : []),
  ];
  const waitEnds = waitingPeriodEnds(benefit, plan);
  return {
    benefitId: benefit.id,
    memberId,
    limits,
    pool,
    frequency,
    remainingCents: moneyRemaining.length ? Math.min(...moneyRemaining) : null,
    usedCents: paidInWindow(ledger, benefit.id, memberId, currentBenefitPeriod(plan, asOf)),
    waitingPeriodEnds: waitEnds,
    inWaitingPeriod: waitEnds != null && asOf < waitEnds,
  };
}

function costSharesFor(plan: Plan, family: boolean) {
  return plan.costShares.filter(
    (cs) => (cs.kind === "deductible" || cs.kind === "excess") && (cs.scope === "per_person") !== family,
  );
}

export function computeUsage(plan: Plan, claims: Claim[], opts: UsageOptions): PlanUsage {
  const ledger = buildLedger(plan, claims, opts);
  const asOf = opts.today;
  const benefits = allBenefits(plan);
  return {
    asOf,
    members: plan.members.map((member) => ({
      memberId: member.id,
      benefits: benefits.map((b) => benefitUsageOnLedger(plan, ledger, b, member.id, asOf)),
      pools: plan.limitPools.map((pool) => poolUsageAt(plan, ledger, pool, member.id, asOf)),
      costShares: costSharesFor(plan, false).map((cs) => costShareUsageAt(plan, ledger, cs, member.id, asOf)),
    })),
    family: { costShares: costSharesFor(plan, true).map((cs) => costShareUsageAt(plan, ledger, cs, null, asOf)) },
  };
}

export function benefitUsage(
  plan: Plan,
  claims: Claim[],
  benefitId: string,
  memberId: string,
  opts: UsageOptions & { asOf?: IsoDate; excludeClaimId?: string },
): BenefitUsage {
  const benefit = indexPlan(plan).benefits.get(benefitId);
  if (!benefit) throw new RangeError(`Unknown benefit: ${benefitId}`);
  const ledger = buildLedger(plan, claims, opts);
  return benefitUsageOnLedger(plan, ledger, benefit, memberId, opts.asOf ?? opts.today);
}

export interface MemberHeadline {
  remainingCents: number;
  paidCents: number;
  usedBenefitIds: string[];
}

/**
 * A pool counts once, as the lesser of its remainder and the sum of its counted benefits' remaining limits.
 * Benefits without a dollar maximum are left out because "unlimited" can't be added up.
 */
export function memberHeadline(plan: Plan, member: MemberUsage, include: "used" | "all" = "used"): MemberHeadline {
  const used = member.benefits.filter((b) => b.usedCents > 0);
  const counted = include === "all" ? member.benefits.filter((b) => !b.inWaitingPeriod) : used;
  const byPool = new Map<string, { pool: PoolUsage; sum: number }>();
  let remaining = 0;
  for (const usage of counted) {
    const own = usage.limits.filter((l) => l.limit.unit === "cents" && l.window).map((l) => l.remaining);
    const ownRemaining = own.length ? Math.min(...own) : null;
    const pool = usage.pool && usage.pool.limit.unit === "cents" && usage.pool.window ? usage.pool : null;
    if (pool) {
      const entry = byPool.get(pool.poolId) ?? { pool, sum: 0 };
      entry.sum += ownRemaining ?? pool.remaining;
      byPool.set(pool.poolId, entry);
    } else if (ownRemaining != null) {
      remaining += ownRemaining;
    }
  }
  for (const { pool, sum } of byPool.values()) remaining += Math.min(pool.remaining, sum);
  return {
    remainingCents: remaining,
    paidCents: member.benefits.reduce((s, b) => s + b.usedCents, 0),
    usedBenefitIds: used.map((b) => b.benefitId),
  };
}
