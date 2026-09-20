import Link from "next/link";
import type { ReactNode } from "react";

import { Chip, Meter, Surface } from "@/components/ui";
import { date, frequencyText, money, STATUS_LABEL } from "@/domain/describe";
import { indexPlan } from "@/domain/ledger";
import { nextResetDate } from "@/domain/periods";
import type { CostShareUsage, MemberUsage, PlanUsage } from "@/domain/usage";
import type { Claim, Member, Plan } from "@/domain/types";
import { cn } from "@/lib/cn";

import { daysLeftText, deadlineRuleText, draftDeadlines, outOfPocketThisYear, type Tone } from "./model";
import { TONE_TEXT } from "./BenefitList";

function Card({ title, children, className, description }: { title: string; description?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Surface aria-label={title} className={cn("sm:p-8", className)}>
      <h2 className="text-xl font-medium tracking-tight text-ink">{title}</h2>
      {description && <p className="mt-1 text-base text-muted">{description}</p>}
      <div className="mt-4">{children}</div>
    </Surface>
  );
}

function Row({ title, detail, value, tone = "ink", children, href }: { title: ReactNode; detail?: ReactNode; value?: ReactNode; tone?: Tone; children?: ReactNode; href?: string }) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-base font-medium text-ink">{title}</p>
          {detail && <p className="text-sm text-muted">{detail}</p>}
        </div>
        {value && <p className={cn("shrink-0 text-right text-base font-medium", TONE_TEXT[tone])}>{value}</p>}
      </div>
      {children}
    </>
  );
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      {href ? (
        <Link href={href} className="-mx-3 block rounded-tile px-3 py-1 hover:bg-sunken/70">
          {body}
        </Link>
      ) : (
        body
      )}
    </li>
  );
}

export function DeadlinesCard({ plan, claims, today }: { plan: Plan; claims: Claim[]; today: string }) {
  const items = draftDeadlines(claims, today);
  const awaiting = claims.filter((c) => c.status === "submitted").length;
  const rule = deadlineRuleText(plan, today);
  const index = indexPlan(plan);
  const alias = (id: string) => plan.members.find((m) => m.id === id)?.alias ?? "";
  if (items.length === 0 && awaiting === 0 && !rule) return null;
  return (
    <Card title="Claim deadlines" description={rule}>
      {items.length > 0 ? (
        <ul className="divide-y divide-line">
          {items.map(({ claim, deadline, daysLeft, tone }) => {
            const benefit = index.benefits.get(claim.lines[0]?.benefitId ?? "");
            return (
              <Row
                key={claim.id}
                href={`/claims?id=${claim.id}`}
                title={`${benefit?.name ?? "Claim"} for ${alias(claim.patientMemberId)}`}
                detail={`Draft, send by ${date(deadline, plan.currency)}`}
                value={daysLeftText(daysLeft)}
                tone={tone === "muted" ? "ink" : tone}
              />
            );
          })}
        </ul>
      ) : (
        <p className="text-base text-ink-2">No drafts waiting to be sent.</p>
      )}
      {awaiting > 0 && (
        <Link href="/claims" className="mt-4 inline-flex items-center gap-2 text-base text-ink-2 underline decoration-line underline-offset-4 hover:decoration-ink">
          {awaiting} {awaiting === 1 ? "claim is" : "claims are"} {STATUS_LABEL.submitted.toLowerCase()} and awaiting a decision
        </Link>
      )}
    </Card>
  );
}

export function PoolsCard({ plan, member }: { plan: Plan; member: MemberUsage }) {
  if (member.pools.length === 0) return null;
  const c = plan.currency;
  const index = indexPlan(plan);
  return (
    <Card title="Shared maximums" description="Benefits in a group draw from one amount.">
      <ul className="divide-y divide-line">
        {member.pools.map((pool) => {
          const cents = pool.limit.unit === "cents";
          const names = pool.benefitIds.map((id) => index.benefits.get(id)?.name).filter(Boolean).join(", ");
          return (
            <Row
              key={pool.poolId}
              title={pool.name}
              detail={names}
              value={cents ? `${money(pool.remaining, c)} left` : `${pool.remaining} left`}
              tone={pool.remaining === 0 ? "negative" : "ink"}
            >
              <Meter
                used={pool.used}
                max={pool.limit.value}
                label={`${pool.name}: ${cents ? money(pool.used, c) : pool.used} used of ${cents ? money(pool.limit.value, c) : pool.limit.value}`}
                className="mt-3"
              />
              <p className="mt-2 text-sm text-muted">
                {cents ? money(pool.used, c) : pool.used} used of {cents ? money(pool.limit.value, c) : pool.limit.value}
              </p>
            </Row>
          );
        })}
      </ul>
    </Card>
  );
}

export function FrequencyCard({ plan, member }: { plan: Plan; member: MemberUsage }) {
  const index = indexPlan(plan);
  const rows = member.benefits.filter((b) => b.frequency && index.benefits.get(b.benefitId)?.frequency);
  if (rows.length === 0) return null;
  return (
    <Card title="When you can claim again">
      <ul className="divide-y divide-line">
        {rows.map((usage) => {
          const benefit = index.benefits.get(usage.benefitId)!;
          const next = usage.frequency!.nextEligible;
          return (
            <Row
              key={benefit.id}
              title={benefit.name}
              detail={`Covers ${frequencyText(benefit.frequency!)}`}
              value={next ? date(next, plan.currency) : "Eligible now"}
              tone={next ? "caution" : "positive"}
            />
          );
        })}
      </ul>
    </Card>
  );
}

export function WaitingPeriodsCard({ plan, member }: { plan: Plan; member: MemberUsage }) {
  const index = indexPlan(plan);
  const rows = member.benefits.filter((b) => (index.benefits.get(b.benefitId)?.waitingPeriod?.months ?? 0) > 0 && b.waitingPeriodEnds);
  if (rows.length === 0) return null;
  const waiting = rows.filter((r) => r.inWaitingPeriod);
  const served = rows.filter((r) => !r.inWaitingPeriod);
  const c = plan.currency;
  const row = (usage: (typeof rows)[number]) => {
    const benefit = index.benefits.get(usage.benefitId)!;
    return (
      <Row
        key={benefit.id}
        title={benefit.name}
        detail={`${benefit.waitingPeriod!.months}-month wait${benefit.waitingPeriod!.note ? `, ${benefit.waitingPeriod!.note.charAt(0).toLowerCase()}${benefit.waitingPeriod!.note.slice(1)}` : ""}`}
        value={usage.inWaitingPeriod ? `From ${date(usage.waitingPeriodEnds!, c)}` : "Served"}
        tone={usage.inWaitingPeriod ? "caution" : "positive"}
      />
    );
  };
  return (
    <Card
      title="Waiting periods"
      description={
        waiting.length === 0
          ? served.length === 1
            ? "The only waiting period is served."
            : `All ${served.length} waiting periods are served.`
          : `${waiting.length} still running, counted from ${plan.effectiveDate ? date(plan.effectiveDate, c) : "your coverage start"}.`
      }
    >
      {waiting.length > 0 && <ul className="divide-y divide-line">{waiting.map(row)}</ul>}
      {served.length > 0 && (
        <details className="group/served">
          <summary className="flex h-10 cursor-pointer list-none items-center text-base text-ink-2 hover:text-ink [&::-webkit-details-marker]:hidden">
            <span className="group-open/served:hidden">Show served waiting periods</span>
            <span className="hidden group-open/served:inline">Hide served waiting periods</span>
          </summary>
          <ul className="mt-2 divide-y divide-line">{served.map(row)}</ul>
        </details>
      )}
    </Card>
  );
}

export function CostSharesCard({ plan, usage, member, today }: { plan: Plan; usage: PlanUsage; member: MemberUsage; today: string }) {
  const shares: Array<{ usage: CostShareUsage; family: boolean }> = [
    ...usage.family.costShares.map((u) => ({ usage: u, family: true })),
    ...member.costShares.map((u) => ({ usage: u, family: false })),
  ];
  if (shares.length === 0) return null;
  const c = plan.currency;
  const kinds = new Set(shares.map((s) => plan.costShares.find((cs) => cs.id === s.usage.costShareId)?.kind));
  const title = kinds.size === 1 && kinds.has("excess") ? "Excess" : kinds.size === 1 && kinds.has("deductible") ? "Deductible" : "Deductibles and excess";
  return (
    <Card title={title}>
      <ul className="divide-y divide-line">
        {shares.map(({ usage: u, family }) => {
          const share = plan.costShares.find((cs) => cs.id === u.costShareId)!;
          const amount = share.amountCents ?? 0;
          const met = u.remainingCents === 0;
          const reset = share.period ? nextResetDate(share.period, plan, today) : null;
          return (
            <Row
              key={`${u.costShareId}-${u.memberId ?? "family"}`}
              title={share.name}
              detail={`${money(amount, c)} ${family ? "per family" : "per person"}${reset ? `, resets ${date(reset, c)}` : ""}`}
              value={met ? "Met" : `${money(u.remainingCents, c)} to go`}
              tone={met ? "positive" : "ink"}
            >
              {!met && <Meter used={u.metCents} max={amount} label={`${share.name}: ${money(u.metCents, c)} of ${money(amount, c)} met`} className="mt-3" />}
              <p className="mt-2 text-sm text-muted">
                {money(u.metCents, c)} of {money(amount, c)} {share.kind === "excess" ? "paid" : "met"} this benefit year
              </p>
            </Row>
          );
        })}
      </ul>
    </Card>
  );
}

export function HsaCard({ plan, claims, member, today }: { plan: Plan; claims: Claim[]; member: Member; today: string }) {
  if (plan.profile.kind !== "CA" || !plan.profile.hsa) return null;
  const hsa = plan.profile.hsa;
  const c = plan.currency;
  const outOfPocket = outOfPocketThisYear(plan, claims, member.id, today);
  return (
    <Card title="Health spending account">
      <p className="text-base text-ink-2">
        {money(hsa.annualCreditCents, c)} of credit each benefit year
        {hsa.carryForwardYears > 0 ? `, and unused credit carries forward ${hsa.carryForwardYears} year${hsa.carryForwardYears === 1 ? "" : "s"}` : ""}.
      </p>
      {outOfPocket > 0 && (
        <p className="mt-3 text-base text-ink-2">
          {member.alias} paid <span className="font-medium text-ink">{money(outOfPocket, c)}</span> on decided claims this year that the plan
          didn&apos;t cover. The account can reimburse eligible costs like these.
        </p>
      )}
    </Card>
  );
}

const TIER_LABEL = { gold: "Gold", silver: "Silver", bronze: "Bronze", basic: "Basic" } as const;
const COVER_LABEL = { hospital: "Hospital cover", extras: "Extras cover", combined: "Hospital and extras" } as const;
const CATEGORY_TONE = { covered: "positive", restricted: "caution", excluded: "negative" } as const;
const CATEGORY_LABEL = { covered: "Covered", restricted: "Restricted", excluded: "Not covered" } as const;

export function HospitalCard({ plan }: { plan: Plan }) {
  if (plan.profile.kind !== "AU" || !plan.profile.hospital) return null;
  const hospital = plan.profile.hospital;
  const c = plan.currency;
  return (
    <Card
      title={`${TIER_LABEL[hospital.tier]}${hospital.plus ? " Plus" : ""} hospital`}
      description={`${COVER_LABEL[plan.profile.coverType]}. For information only: Benefura doesn't estimate hospital claims.`}
    >
      {hospital.excess && (
        <p className="text-base text-ink-2">
          {money(hospital.excess.amountCents, c)} excess {hospital.excess.per === "year" ? "a year" : "an admission"}
          {hospital.excess.maxPerYearCents ? `, at most ${money(hospital.excess.maxPerYearCents, c)} a year` : ""}.
        </p>
      )}
      {(["covered", "restricted", "excluded"] as const).map((status) => {
        const group = hospital.categories.filter((cat) => cat.status === status);
        if (group.length === 0) return null;
        return (
          <div key={status} className="mt-4">
            <p className="text-sm text-muted">{CATEGORY_LABEL[status]}</p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {group.map((cat) => (
                <li key={cat.name}>
                  <Chip tone={CATEGORY_TONE[status]}>{cat.name}</Chip>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </Card>
  );
}
