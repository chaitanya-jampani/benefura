// Tools run in the browser against IndexedDB; only their compact, aliased outputs go back to the model.
// Schemas live in apps/api/app/chat/tool_registry.py.
import { db, getSetting, SETTING_KEYS, type ClaimRecord, type PlanRecord } from "@/db/dexie";
import { claimTotals, createDraftClaim, updateClaim, type ClaimActor, type ClaimPatch } from "@/domain/claims";
import { estimateReimbursement } from "@/domain/estimate";
import { currentBenefitPeriod, todayIso } from "@/domain/periods";
import { findBenefits, searchPlanDocument } from "@/domain/search";
import { benefitUsage } from "@/domain/usage";
import type { Benefit, Claim, Coverage, Currency, Frequency, Limit, Member, Period, Plan } from "@/domain/types";
import { formatMoney } from "@/lib/format";

import { loadActivePlanRecord } from "./activePlan";
import type { BrowserToolName, ToolInputs } from "./types";

export const BROWSER_TOOLS: ReadonlySet<string> = new Set<BrowserToolName>([
  "get_plan_overview",
  "find_benefits",
  "search_plan_document",
  "get_usage",
  "estimate_reimbursement",
  "list_claims",
  "draft_claim",
  "update_claim",
]);

export const APPROVAL_TOOLS: ReadonlySet<string> = new Set<BrowserToolName>(["draft_claim", "update_claim"]);

const MAX_RESULTS = 5;
const MAX_USAGE_ROWS = 24;
const MAX_CLAIMS = 25;

export class ToolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolError";
  }
}

export interface AgentProvenance {
  agentName: string | null;
  agentVersion: string | null;
  traceId: string | null;
}

export interface ToolRunOptions {
  provenance?: AgentProvenance;
  now?: Date;
  tz?: string;
}

interface ToolState {
  record: PlanRecord;
  plan: Plan;
  claims: Claim[];
  today: string;
  includeSubmitted: boolean;
  now: Date;
}

async function loadState(opts: ToolRunOptions): Promise<ToolState> {
  const record = await loadActivePlanRecord();
  if (!record) {
    throw new ToolError("No plan is loaded yet. Ask the user to try the demo or add their booklet first.");
  }
  const [claims, includeSubmitted] = await Promise.all([
    db.claims.where("planId").equals(record.id).toArray(),
    getSetting<boolean>(SETTING_KEYS.includeSubmittedClaims, false),
  ]);
  return {
    record,
    plan: record.plan,
    claims,
    includeSubmitted,
    now: opts.now ?? new Date(),
    today: todayIso(opts.tz),
  };
}

function periodText(period: Period): string {
  switch (period.kind) {
    case "benefit_year":
      return "per benefit year";
    case "policy_anniversary":
      return "per policy year";
    case "rolling_months":
      return `in any ${period.months ?? 12} months`;
    case "consecutive_benefit_years":
      return `every ${period.years ?? 2} benefit years`;
    case "lifetime":
      return "lifetime";
    case "per_visit":
      return "per visit";
    case "per_admission":
      return "per admission";
  }
}

const SCOPE_TEXT = { per_person: "per person", per_family: "per family", per_policy: "per policy" } as const;

function amountText(unit: Limit["unit"], value: number, currency: Currency): string {
  return unit === "cents" ? formatMoney(value, currency) : `${value} ${unit}`;
}

function limitText(limit: Limit, currency: Currency): string {
  return `${amountText(limit.unit, limit.value, currency)} ${SCOPE_TEXT[limit.scope]} ${periodText(limit.period)}`;
}

function frequencyText(frequency: Frequency): string {
  return `${frequency.count} ${frequency.count === 1 ? "service" : "services"} ${SCOPE_TEXT[frequency.scope]} ${periodText(frequency.period)}`;
}

function coverageText(coverage: Coverage, currency: Currency): string {
  switch (coverage.kind) {
    case "percent":
      return `${coverage.percent ?? 0}%`;
    case "percent_capped":
      return `${coverage.percent ?? 0}% up to ${formatMoney(coverage.capCents ?? 0, currency)} per visit`;
    case "fixed_per_service":
      return `${formatMoney(coverage.amountCents ?? 0, currency)} per service`;
    case "per_diem":
      return `${formatMoney(coverage.amountCents ?? 0, currency)} per day`;
    case "schedule":
      return coverage.scheduleItems.length
        ? `Set benefit per item (${coverage.scheduleItems.length} item codes)`
        : (coverage.scheduleNote ?? (coverage.percent != null ? `${coverage.percent}%` : "Set benefit per item"));
  }
}

function allBenefits(plan: Plan): Benefit[] {
  return plan.categories.flatMap((c) => c.benefits);
}

function benefitById(plan: Plan, benefitId: string): Benefit {
  const benefit = allBenefits(plan).find((b) => b.id === benefitId);
  if (!benefit) throw new ToolError(`Unknown benefit id "${benefitId}". Call find_benefits first.`);
  return benefit;
}

function aliasKey(alias: string): string {
  return alias.replace(/[[\]\s]/g, "").toUpperCase();
}

function memberByAlias(plan: Plan, alias: string): Member {
  const key = aliasKey(alias);
  const member = plan.members.find((m) => aliasKey(m.alias) === key || m.id === alias);
  if (!member) {
    const known = plan.members.map((m) => m.alias).join(", ") || "none";
    throw new ToolError(`Unknown member alias "${alias}". Members on this plan: ${known}.`);
  }
  return member;
}

function memberAlias(plan: Plan, memberId: string): string {
  return plan.members.find((m) => m.id === memberId)?.alias ?? "[UNKNOWN_MEMBER]";
}

function describeBenefit(plan: Plan, benefit: Benefit) {
  const category = plan.categories.find((c) => c.id === benefit.categoryId);
  const pool = benefit.poolId ? plan.limitPools.find((p) => p.id === benefit.poolId) : undefined;
  return {
    benefitId: benefit.id,
    name: benefit.name,
    category: category?.name ?? null,
    coverage: coverageText(benefit.coverage, plan.currency),
    limits: benefit.limits.map((l) => limitText(l, plan.currency)),
    pool: pool ? `${pool.name}: ${limitText(pool.limit, plan.currency)}` : null,
    frequency: benefit.frequency ? frequencyText(benefit.frequency) : null,
    waitingPeriodMonths: benefit.waitingPeriod?.months ?? null,
    requirements: benefit.requirements,
    source: benefit.source ? { page: benefit.source.page, quote: benefit.source.quote } : null,
  };
}

function isoDate(value: string, field: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new ToolError(`${field} must be an ISO date (YYYY-MM-DD).`);
  return value;
}

function cents(value: number, field: string): number {
  if (!Number.isInteger(value) || value < 0) throw new ToolError(`${field} must be a whole number of cents.`);
  return value;
}

function optionalCents(value: number | null | undefined, field: string): number {
  return value === null || value === undefined ? 0 : cents(value, field);
}

function summarizeClaim(plan: Plan, claim: Claim) {
  const totals = claimTotals(claim);
  return {
    claimId: claim.id,
    status: claim.status,
    memberAlias: memberAlias(plan, claim.patientMemberId),
    provider: claim.provider ?? null,
    serviceDates: [...new Set(claim.lines.map((l) => l.serviceDate))].sort(),
    benefitIds: [...new Set(claim.lines.map((l) => l.benefitId))],
    chargedCents: totals.chargedCents,
    paidCents: totals.paidCents,
    deadline: claim.deadline ?? null,
    updatedAt: claim.updatedAt,
  };
}

type Handler<K extends BrowserToolName> = (input: ToolInputs[K], state: ToolState, opts: ToolRunOptions) => Promise<unknown> | unknown;

const handlers: { [K in BrowserToolName]: Handler<K> } = {
  get_plan_overview(_input, { plan, today }) {
    return {
      insurer: plan.insurer,
      planName: plan.planName,
      region: plan.region,
      currency: plan.currency,
      today,
      benefitPeriod: currentBenefitPeriod(plan, today),
      members: plan.members.map((m) => ({ alias: m.alias, relationship: m.relationship })),
      categories: plan.categories.map((c) => ({
        name: c.name,
        benefits: c.benefits.map((b) => ({ benefitId: b.id, name: b.name })),
      })),
      claimRules: {
        submissionDays: plan.claimRules.submissionDays ?? null,
        daysAfterPeriodEnd: plan.claimRules.daysAfterPeriodEnd ?? null,
        receiptsRequired: plan.claimRules.receiptsRequired,
      },
    };
  },

  find_benefits({ query }, { plan }) {
    const hits = findBenefits(plan, query, MAX_RESULTS);
    return { query, benefits: hits.map((hit) => describeBenefit(plan, benefitById(plan, hit.benefitId))) };
  },

  search_plan_document({ query }, { plan }) {
    const hits = searchPlanDocument(plan, query, MAX_RESULTS);
    return { query, results: hits.map((h) => ({ page: h.page, quote: h.quote, benefitId: h.benefitId })) };
  },

  get_usage({ benefit_id, member_alias }, { plan, claims, today, includeSubmitted }) {
    const members = member_alias ? [memberByAlias(plan, member_alias)] : plan.members;
    const benefits = benefit_id
      ? [benefitById(plan, benefit_id)]
      : allBenefits(plan).filter((b) => b.limits.length > 0 || b.poolId || b.frequency);
    const rows = [];
    for (const benefit of benefits) {
      for (const member of members) {
        if (rows.length >= MAX_USAGE_ROWS) break;
        const usage = benefitUsage(plan, claims, benefit.id, member.id, { today, includeSubmitted });
        rows.push({
          benefitId: benefit.id,
          name: benefit.name,
          memberAlias: member.alias,
          remainingCents: usage.remainingCents,
          usedCents: usage.usedCents,
          limits: usage.limits.map((l) => ({
            limit: limitText(l.limit, plan.currency),
            unit: l.limit.unit,
            used: l.used,
            remaining: l.remaining,
            window: l.window,
          })),
          pool: usage.pool
            ? { name: usage.pool.name, used: usage.pool.used, remaining: usage.pool.remaining, window: usage.pool.window }
            : null,
          frequency: usage.frequency
            ? { count: usage.frequency.count, used: usage.frequency.used, nextEligible: usage.frequency.nextEligible }
            : null,
          inWaitingPeriod: usage.inWaitingPeriod,
          waitingPeriodEnds: usage.waitingPeriodEnds,
        });
      }
    }
    return { asOf: today, includesSubmittedClaims: includeSubmitted, usage: rows, truncated: rows.length >= MAX_USAGE_ROWS };
  },

  estimate_reimbursement(input, { plan, claims, today, includeSubmitted }) {
    const benefit = benefitById(plan, input.benefit_id);
    const member = memberByAlias(plan, input.member_alias);
    const result = estimateReimbursement(
      plan,
      claims,
      {
        benefitId: benefit.id,
        memberId: member.id,
        serviceDate: isoDate(input.service_date, "service_date"),
        chargedCents: cents(input.charged_cents, "charged_cents"),
        quantity: input.quantity ?? undefined,
        itemCode: input.item_code,
        otherPlanPaidCents: optionalCents(input.other_plan_paid_cents, "other_plan_paid_cents"),
      },
      { today, includeSubmitted },
    );
    return {
      benefitId: benefit.id,
      name: benefit.name,
      memberAlias: member.alias,
      serviceDate: input.service_date,
      chargedCents: input.charged_cents,
      otherPlanPaidCents: input.other_plan_paid_cents ?? 0,
      planPaysCents: result.planPaysCents,
      memberPaysCents: result.memberPaysCents,
      limitedBy: result.limitedBy,
      steps: result.steps.map((s) => ({ label: s.label, planPaysCents: s.planPaysCents })),
      warnings: result.warnings,
      remainingAfter: result.remainingAfter,
      deadline: result.deadline,
    };
  },

  list_claims({ status, benefit_id }, { plan, claims }) {
    const matching = claims
      .filter((c) => (status ? c.status === status : true))
      .filter((c) => (benefit_id ? c.lines.some((l) => l.benefitId === benefit_id) : true))
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return {
      total: matching.length,
      claims: matching.slice(0, MAX_CLAIMS).map((c) => summarizeClaim(plan, c)),
    };
  },

  async draft_claim(input, { record, plan, now }, opts) {
    if (!input.lines.length) throw new ToolError("A claim needs at least one line.");
    const member = memberByAlias(plan, input.member_alias);
    const lines = input.lines.map((line, i) => ({
      benefitId: benefitById(plan, line.benefit_id).id,
      serviceDate: isoDate(line.service_date, `lines[${i}].service_date`),
      chargedCents: cents(line.charged_cents, `lines[${i}].charged_cents`),
      quantity: line.quantity && line.quantity > 0 ? line.quantity : 1,
      itemCode: line.item_code,
      description: line.description,
      otherPlanPaidCents: optionalCents(line.other_plan_paid_cents, `lines[${i}].other_plan_paid_cents`),
    }));
    const claim: ClaimRecord = createDraftClaim(
      plan,
      { patientMemberId: member.id, provider: input.provider, lines },
      agentActor(opts),
      now,
    );
    await db.claims.add({ ...claim, planId: record.id });
    const totals = claimTotals(claim);
    return {
      claimId: claim.id,
      status: claim.status,
      memberAlias: member.alias,
      provider: claim.provider ?? null,
      lines: claim.lines.length,
      totalChargedCents: totals.chargedCents,
      deadline: claim.deadline ?? null,
    };
  },

  async update_claim(input, { plan, claims, today, now }, opts) {
    const claim = claims.find((c) => c.id === input.claim_id);
    if (!claim) throw new ToolError(`Unknown claim id "${input.claim_id}". Call list_claims first.`);
    const patch: ClaimPatch = {};
    if (input.status) patch.status = input.status;
    if (input.provider !== null) patch.provider = input.provider;
    if (input.paid_cents !== null) {
      patch.outcome = { paidCents: cents(input.paid_cents, "paid_cents"), decidedOn: today, note: input.note };
    } else if (input.note !== null && claim.outcome) {
      patch.outcome = { ...claim.outcome, note: input.note };
    }
    if (Object.keys(patch).length === 0) throw new ToolError("Nothing to update.");
    const updated = updateClaim(plan, claim, patch, agentActor(opts), now);
    await db.claims.put(updated);
    return summarizeClaim(plan, updated);
  },
};

function agentActor(opts: ToolRunOptions): ClaimActor {
  return {
    actor: "agent",
    agentName: opts.provenance?.agentName ?? null,
    agentVersion: opts.provenance?.agentVersion ?? null,
    traceId: opts.provenance?.traceId ?? null,
  };
}

export function isBrowserTool(name: string): name is BrowserToolName {
  return BROWSER_TOOLS.has(name);
}

export async function runBrowserTool(name: string, input: unknown, opts: ToolRunOptions = {}): Promise<unknown> {
  if (!isBrowserTool(name)) throw new ToolError(`${name} is not a browser tool.`);
  const state = await loadState(opts);
  const handler = handlers[name] as Handler<BrowserToolName>;
  try {
    return await handler((input ?? {}) as never, state, opts);
  } catch (err) {
    if (err instanceof ToolError) throw err;
    throw new ToolError(err instanceof Error ? err.message : "The tool failed.");
  }
}
