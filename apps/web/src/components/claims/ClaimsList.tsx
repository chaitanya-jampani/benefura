import { Plus, ScanLine } from "lucide-react";
import Link from "next/link";

import { BenefitGlyph } from "@/components/dashboard/BenefitIcon";
import { daysLeftText } from "@/components/dashboard/model";
import { DemoBanner } from "@/components/demo/DemoBanner";
import { buttonStyles, HeroAmount, ListRow, Surface, Trend } from "@/components/ui";
import type { PlanRecord } from "@/db/dexie";
import { groupClaimsByStatus, latestServiceDate } from "@/domain/claims";
import { date, money } from "@/domain/describe";
import { estimateClaim } from "@/domain/estimate";
import { indexPlan } from "@/domain/ledger";
import { currentBenefitPeriod, daysBetween } from "@/domain/periods";
import type { Claim } from "@/domain/types";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/format";

const GROUP_TITLE = {
  draft: "Drafts to send",
  submitted: "Awaiting a decision",
  partially_paid: "Partially paid",
  paid: "Paid",
  rejected: "Rejected",
} as const;

export function ClaimsList({ record, claims, today }: { record: PlanRecord; claims: Claim[]; today: string }) {
  const plan = record.plan;
  const c = plan.currency;
  const index = indexPlan(plan);
  const year = currentBenefitPeriod(plan, today);
  const groups = groupClaimsByStatus(claims);
  const inYear = (claim: Claim) => (latestServiceDate(claim) ?? "") >= year.start;
  const paidThisYear = claims.filter((cl) => inYear(cl) && cl.outcome).reduce((s, cl) => s + (cl.outcome?.paidCents ?? 0), 0);
  const awaiting = claims.filter((cl) => cl.status === "submitted");
  const pending = (claim: Claim) => estimateClaim(plan, claims, claim, { today, includeSubmitted: false, excludeClaimId: claim.id }).planPaysCents;
  const awaitingCents = awaiting.reduce((s, cl) => s + pending(cl), 0);
  const drafts = claims.filter((cl) => cl.status === "draft");
  const decidedThisYear = claims.filter((cl) => inYear(cl) && cl.outcome).length;
  const alias = (id: string) => plan.members.find((m) => m.id === id)?.alias ?? "";

  return (
    <div className="space-y-6">
      {record.isDemo && <DemoBanner region={plan.region} />}
      <header className="flex flex-wrap items-end justify-between gap-4 px-1">
        <div>
          <p className="text-base text-muted">{plan.insurer}</p>
          <h1 className="text-3xl font-medium tracking-tight text-ink sm:text-4xl">Claims</h1>
        </div>
        <div className="flex flex-wrap gap-3">
          <Link href="/claims?id=new&receipt=1" className={buttonStyles({ variant: "soft" })}>
            <ScanLine aria-hidden strokeWidth={1.5} className="size-5" />
            Add receipt
          </Link>
          <Link href="/claims?id=new" className={buttonStyles({ variant: "primary" })}>
            <Plus aria-hidden strokeWidth={1.5} className="size-5" />
            New claim
          </Link>
        </div>
      </header>

      <Surface glow aria-labelledby="claims-hero" className="grid gap-8 md:grid-cols-[minmax(0,1fr)_minmax(0,18rem)] md:items-end">
        <div>
          <h2 id="claims-hero" className="text-lg text-ink-2">
            Paid back since {date(year.start, c)}
          </h2>
          <p className="mt-6">
            <HeroAmount cents={paidThisYear} currency={c} />
          </p>
          <div className="mt-4">
            {awaiting.length > 0 ? (
              <Trend
                value={`${money(awaitingCents, c)} on the way`}
                caption={`estimated from ${awaiting.length} submitted claim${awaiting.length === 1 ? "" : "s"}`}
              />
            ) : (
              <Trend tone="muted" value="Nothing awaiting a decision" />
            )}
          </div>
        </div>
        <dl className="grid grid-cols-3 gap-3 md:grid-cols-1">
          {[
            ["Drafts to send", drafts.length],
            ["Awaiting a decision", awaiting.length],
            ["Decided this year", decidedThisYear],
          ].map(([label, value]) => (
            <div key={label} className="rounded-tile bg-surface/70 px-4 py-3 shadow-tile md:flex md:items-baseline md:justify-between">
              <dt className="text-sm text-muted">{label}</dt>
              <dd className="text-2xl font-light text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      </Surface>

      {groups.length === 0 ? (
        <Surface>
          <p className="text-lg text-ink-2">No claims yet. Start one by hand or from a receipt.</p>
        </Surface>
      ) : (
        <Surface aria-label="All claims" className="sm:p-8">
          <div className="space-y-8">
            {groups.map((group) => (
              <section key={group.status} aria-labelledby={`group-${group.status}`}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <h2 id={`group-${group.status}`} className="text-xl font-medium tracking-tight text-ink">
                    {GROUP_TITLE[group.status]}
                  </h2>
                  <p className="text-base text-muted">
                    {group.claims.length} claim{group.claims.length === 1 ? "" : "s"}, {money(group.chargedCents, c)} charged
                    {group.status === "paid" || group.status === "partially_paid"
                      ? `, ${money(group.paidCents, c)} paid`
                      : group.status === "rejected"
                        ? ""
                        : `, about ${money(group.claims.reduce((sum, cl) => sum + pending(cl), 0), c)} expected`}
                  </p>
                </div>
                <ul className="mt-2 divide-y divide-line">
                  {group.claims.map((claim) => {
                    const first = index.benefits.get(claim.lines[0]?.benefitId ?? "");
                    const names = [...new Set(claim.lines.map((l) => index.benefits.get(l.benefitId)?.name).filter(Boolean))];
                    const served = latestServiceDate(claim);
                    const charged = claim.lines.reduce((s, l) => s + l.chargedCents, 0);
                    const decided = claim.outcome != null;
                    const estimate = decided ? 0 : pending(claim);
                    const soon = claim.status === "draft" && claim.deadline ? daysBetween(today, claim.deadline) : null;
                    return (
                      <li key={claim.id}>
                        <ListRow
                          href={`/claims?id=${claim.id}`}
                          icon={<BenefitGlyph benefit={first ?? null} kind={first ? index.categories.get(first.categoryId)?.kind : undefined} />}
                          title={<span className="block whitespace-normal hyphens-auto">{claim.provider ?? names[0] ?? "Claim"}</span>}
                          subtitle={
                            <span className="line-clamp-2 whitespace-normal">
                              {[alias(claim.patientMemberId), names.join(", "), served ? date(served, c) : null].filter(Boolean).join(", ")}
                            </span>
                          }
                          trailing={
                            <span className="flex flex-col items-end leading-tight">
                              <span className={cn("font-medium", claim.status === "rejected" && "text-negative")}>
                                {formatMoney(decided ? (claim.outcome?.paidCents ?? 0) : estimate, c)}
                              </span>
                              <span className={cn("mt-1 text-sm", soon !== null && soon <= 30 ? "text-caution" : "text-muted")}>
                                {soon !== null && soon <= 30 ? daysLeftText(soon) : `of ${money(charged, c)}`}
                              </span>
                            </span>
                          }
                        />
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        </Surface>
      )}
    </div>
  );
}
