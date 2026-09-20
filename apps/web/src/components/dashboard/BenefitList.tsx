import { ChevronDown } from "lucide-react";
import Link from "next/link";

import { buttonStyles, ListRow, Meter, SectionTitle, Surface } from "@/components/ui";
import { benefitRuleText, currentPeriodText, date, frequencyText, limitText, money } from "@/domain/describe";
import { nextResetDate } from "@/domain/periods";
import type { Member, Plan } from "@/domain/types";
import { cn } from "@/lib/cn";

import { AskLink } from "./AskLink";
import { BenefitGlyph } from "./BenefitIcon";
import type { BenefitRow, Tone } from "./model";

export const TONE_TEXT: Record<Tone, string> = {
  ink: "text-ink",
  muted: "text-muted",
  positive: "text-positive",
  caution: "text-caution",
  negative: "text-negative",
};

export function BenefitList({ plan, member, rows, today }: { plan: Plan; member: Member; rows: BenefitRow[]; today: string }) {
  const inUse = rows.filter((r) => r.inUse);
  const others = rows.filter((r) => !r.inUse);
  return (
    <Surface aria-label="Benefits">
      <SectionTitle>Benefits</SectionTitle>
      <p className="mt-1 text-base text-muted">
        {inUse.length > 0
          ? `${inUse.length} in use for ${member.alias}. Select one for the rules and the booklet quote.`
          : `Nothing claimed for ${member.alias} yet. Select a benefit for its rules.`}
      </p>
      {inUse.length > 0 && (
        <ul className="mt-4 divide-y divide-line">
          {inUse.map((row) => (
            <BenefitItem key={row.benefit.id} plan={plan} member={member} row={row} today={today} />
          ))}
        </ul>
      )}
      {others.length > 0 && (
        <details className="group/more mt-2" open={inUse.length === 0}>
          <summary className="flex h-12 cursor-pointer list-none items-center gap-2 rounded-full text-base text-ink-2 hover:text-ink [&::-webkit-details-marker]:hidden">
            <ChevronDown aria-hidden strokeWidth={1.5} className="size-5 transition-transform group-open/more:rotate-180" />
            <span className="group-open/more:hidden">
              {inUse.length > 0 ? `Show ${others.length} more benefits` : `Show all ${others.length} benefits`}
            </span>
            <span className="hidden group-open/more:inline">Hide benefits not in use</span>
          </summary>
          <ul className="divide-y divide-line">
            {others.map((row) => (
              <BenefitItem key={row.benefit.id} plan={plan} member={member} row={row} today={today} />
            ))}
          </ul>
        </details>
      )}
    </Surface>
  );
}

function BenefitItem({ plan, member, row, today }: { plan: Plan; member: Member; row: BenefitRow; today: string }) {
  const { benefit, usage, category } = row;
  return (
    <li>
      <details className="group/benefit">
        <summary className="-mx-3 cursor-pointer list-none rounded-tile px-3 hover:bg-sunken/70 [&::-webkit-details-marker]:hidden">
          <ListRow
            icon={<BenefitGlyph benefit={benefit} kind={category.kind} />}
            title={<span className="block whitespace-normal hyphens-auto">{benefit.name}</span>}
            subtitle={<span className="line-clamp-2 whitespace-normal">{row.subtitle}</span>}
            trailing={
              <span className="flex flex-col items-end leading-tight">
                <span className={cn("font-medium", TONE_TEXT[row.tone])}>{row.value}</span>
                {row.caption && <span className="mt-1 max-w-[6.5rem] text-sm whitespace-normal text-muted sm:max-w-none">{row.caption}</span>}
              </span>
            }
          >
            {row.meter && <Meter used={row.meter.used} max={row.meter.max} label={row.meter.label} className="mt-2" />}
          </ListRow>
        </summary>
        <BenefitDetail plan={plan} member={member} row={row} today={today} usageWaitingEnds={usage.waitingPeriodEnds} />
      </details>
    </li>
  );
}

function BenefitDetail({
  plan,
  member,
  row,
  today,
  usageWaitingEnds,
}: {
  plan: Plan;
  member: Member;
  row: BenefitRow;
  today: string;
  usageWaitingEnds: string | null;
}) {
  const { benefit, usage } = row;
  const c = plan.currency;
  const facts: Array<[string, string]> = [];
  for (const limit of usage.limits) {
    if (limit.limit.unit === "cents" && limit.window) {
      const reset = nextResetDate(limit.limit.period, plan, today);
      facts.push([
        `Maximum, ${limitText(limit.limit, c)}`,
        `${money(limit.used, c)} used ${currentPeriodText(limit.limit.period)}${reset ? `, resets ${date(reset, c)}` : ""}`,
      ]);
    } else if (limit.window) {
      facts.push([`Maximum, ${limitText(limit.limit, c)}`, `${limit.used} used, ${limit.remaining} left`]);
    } else {
      facts.push(["Maximum", `Up to ${limitText(limit.limit, c)}`]);
    }
  }
  if (usage.pool) {
    facts.push([`Shares the ${usage.pool.name.toLowerCase()}`, `${money(usage.pool.used, c)} used of ${money(usage.pool.limit.value, c)}`]);
  }
  if (benefit.frequency && usage.frequency) {
    facts.push([
      `How often, ${frequencyText(benefit.frequency)}`,
      usage.frequency.nextEligible ? `Next eligible ${date(usage.frequency.nextEligible, c)}` : "Eligible now",
    ]);
  }
  if (benefit.waitingPeriod && benefit.waitingPeriod.months > 0) {
    facts.push([
      `Waiting period, ${benefit.waitingPeriod.months} months`,
      usageWaitingEnds ? `${usage.inWaitingPeriod ? "Covered from" : "Served on"} ${date(usageWaitingEnds, c)}` : "Counted from your coverage start",
    ]);
  }
  const question = `How much can ${member.alias} still claim for ${benefit.name.toLowerCase()}, and what are the rules?`;

  return (
    <div className="mb-4 rounded-tile bg-sunken p-5 sm:ml-[5.5rem] sm:p-6">
      <p className="text-base text-ink">{benefitRuleText(benefit, c)}</p>
      {facts.length > 0 && (
        <dl className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-2">
          {facts.map(([term, value]) => (
            <div key={term} className="min-w-0">
              <dt className="text-sm text-muted">{term}</dt>
              <dd className="text-base text-ink-2">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {benefit.requirements.length > 0 && (
        <div className="mt-4">
          <p className="text-sm text-muted">Requirements</p>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-base text-ink-2 marker:text-faint">
            {benefit.requirements.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {benefit.notes && <p className="mt-4 text-base text-ink-2">{benefit.notes}</p>}
      {benefit.source && (
        <figure className="mt-4 border-l-2 border-line pl-4">
          <blockquote className="text-base text-ink-2">{benefit.source.quote}</blockquote>
          <figcaption className="mt-1 text-sm text-muted">Booklet page {benefit.source.page}</figcaption>
        </figure>
      )}
      <div className="no-print mt-5 flex flex-wrap items-center gap-2">
        <Link href={`/claims?id=new&member=${member.id}&benefit=${benefit.id}`} className={buttonStyles({ variant: "soft", size: "sm" })}>
          Start a claim
        </Link>
        <AskLink question={question} />
      </div>
    </div>
  );
}
