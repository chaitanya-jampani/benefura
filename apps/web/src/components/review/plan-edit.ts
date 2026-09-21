import type { AliasRecord } from "@/db/dexie";
import type { AnalyzeChunkResponse, Benefit, Coverage, Currency, Issue, Limit, Member, Period, Plan } from "@/domain/types";
import { formatMoney } from "@/lib/format";

export type PeriodKind = Period["kind"];
export type LimitUnit = Limit["unit"];

export const PERIOD_LABELS: Record<PeriodKind, string> = {
  benefit_year: "Benefit year",
  policy_anniversary: "Policy year",
  rolling_months: "Rolling months",
  consecutive_benefit_years: "Consecutive years",
  lifetime: "Lifetime",
  per_visit: "Per visit",
  per_admission: "Per admission",
};

export const COVERAGE_LABELS: Record<Coverage["kind"], string> = {
  percent: "Percent",
  percent_capped: "Percent up to a cap",
  fixed_per_service: "Fixed per visit",
  per_diem: "Per day",
  schedule: "Schedule",
};

export const UNIT_LABELS: Record<LimitUnit, string> = {
  cents: "Dollars",
  visits: "Visits",
  items: "Items",
  days: "Days",
  hours: "Hours",
};

/** The extraction only knows tokens it happened to read; the user's aliases carry every member and relationship. */
export function withAliasMembers(plan: Plan, aliases: readonly AliasRecord[]): Plan {
  const byAlias = new Map(plan.members.map((m) => [m.alias, m]));
  for (const a of aliases) {
    if (a.kind !== "member" || !/^\[MEMBER_[A-Z]+\]$/.test(a.token)) continue;
    const existing = byAlias.get(a.token);
    byAlias.set(a.token, {
      id: existing?.id ?? `m-${a.token.slice(8, -1).toLowerCase()}`,
      alias: a.token,
      relationship: a.relationship ?? existing?.relationship ?? "dependent",
    });
  }
  if (byAlias.size === 0) byAlias.set("[MEMBER_A]", { id: "m-a", alias: "[MEMBER_A]", relationship: "self" });
  const members: Member[] = [...byAlias.values()].sort((x, y) => x.alias.length - y.alias.length || x.alias.localeCompare(y.alias));
  const identifiers = new Set(plan.identifiers);
  for (const a of aliases) if (/^\[(POLICY|CERT)_\d+\]$/.test(a.token)) identifiers.add(a.token);
  return { ...plan, members, identifiers: [...identifiers].sort() };
}

export interface BenefitRow {
  benefit: Benefit;
  categoryName: string;
}

export function flattenBenefits(plan: Plan): BenefitRow[] {
  return plan.categories.flatMap((c) => c.benefits.map((benefit) => ({ benefit, categoryName: c.name })));
}

export function updateBenefit(plan: Plan, benefitId: string, fn: (b: Benefit) => Benefit): Plan {
  return {
    ...plan,
    categories: plan.categories.map((c) => ({
      ...c,
      benefits: c.benefits.map((b) => (b.id === benefitId ? fn(b) : b)),
    })),
  };
}

export function setBenefitPool(plan: Plan, benefitId: string, poolId: string | null): Plan {
  const next = updateBenefit(plan, benefitId, (b) => ({ ...b, poolId }));
  return {
    ...next,
    limitPools: next.limitPools.map((p) => {
      const without = p.benefitIds.filter((id) => id !== benefitId);
      return { ...p, benefitIds: p.id === poolId ? [...without, benefitId] : without };
    }),
  };
}

export function defaultPeriod(plan: Plan): Period {
  return { ...plan.benefitPeriod };
}

export function periodFor(kind: PeriodKind, base: Period): Period {
  if (kind === base.kind) return base;
  return {
    kind,
    startMonth: kind === "benefit_year" ? (base.startMonth ?? 1) : null,
    startDay: kind === "benefit_year" ? (base.startDay ?? 1) : null,
    months: kind === "rolling_months" ? (base.months ?? 12) : null,
    years: kind === "consecutive_benefit_years" ? (base.years ?? 2) : null,
  };
}

export function dollarsToCents(input: string): number | null {
  const cleaned = input.replace(/[$,\s]/g, "");
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 100) : null;
}

export function centsToDollarsInput(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "";
  return cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2);
}

export function describeCoverage(c: Coverage, currency: Currency): string {
  switch (c.kind) {
    case "percent":
      return `${c.percent ?? 0}%`;
    case "percent_capped":
      return `${c.percent ?? 0}% up to ${formatMoney(c.capCents ?? 0, currency, { whole: (c.capCents ?? 0) % 100 === 0 })}`;
    case "fixed_per_service":
      return `${formatMoney(c.amountCents ?? 0, currency)} per visit`;
    case "per_diem":
      return `${formatMoney(c.amountCents ?? 0, currency)} per day`;
    case "schedule":
      return c.scheduleNote ?? "Schedule of benefits";
  }
}

export function describeLimit(limit: Limit | undefined, currency: Currency): string {
  if (!limit) return "No limit";
  const value = limit.unit === "cents" ? formatMoney(limit.value, currency, { whole: limit.value % 100 === 0 }) : `${limit.value} ${limit.unit}`;
  return `${value}, ${PERIOD_LABELS[limit.period.kind].toLowerCase()}`;
}

export const LOW_CONFIDENCE = 0.7;

export function needsCheck(b: Benefit): boolean {
  return !!b.source && (b.source.confidence < LOW_CONFIDENCE || b.source.verifierVerdict === "unsupported");
}

export interface RowSource {
  quote: string;
  page: number;
  confidence: number;
}

export function rowSources(responses: readonly AnalyzeChunkResponse[]): Map<string, RowSource> {
  const map = new Map<string, RowSource>();
  for (const r of responses) {
    const groups = [r.rows.header, r.rows.benefits, r.rows.pools, r.rows.cost_shares, r.rows.rules, r.rows.hospital_categories];
    for (const rows of groups) {
      for (const row of rows ?? []) {
        map.set(row.meta.rowId, { quote: row.quote, page: row.page, confidence: row.meta.confidence });
        if (row.row_id !== row.meta.rowId) map.set(row.row_id, { quote: row.quote, page: row.page, confidence: row.meta.confidence });
      }
    }
  }
  return map;
}

export type IssueGroup = "personal" | "excluded" | "rows" | "other";

export function issueGroup(issue: Issue): IssueGroup {
  switch (issue.code) {
    case "pii_advisory":
      return "personal";
    case "page_excluded":
    case "prompt_injection":
    case "ocr_empty":
      return "excluded";
    case "unsupported_row":
    case "low_confidence":
    case "ungrounded_quote":
    case "verifier_rounds_exhausted":
      return "rows";
    default:
      return "other";
  }
}

/** De-duplicates because overlapping chunks report the same page twice. */
export function mergeIssues(...lists: ReadonlyArray<readonly Issue[] | undefined>): Issue[] {
  const seen = new Set<string>();
  const out: Issue[] = [];
  for (const list of lists) {
    for (const issue of list ?? []) {
      const key = [issue.code, issue.page ?? "", issue.category ?? "", issue.rowId ?? "", issue.code === "pii_advisory" ? "" : issue.message].join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(issue);
    }
  }
  return out.sort((a, b) => (a.page ?? 0) - (b.page ?? 0));
}
