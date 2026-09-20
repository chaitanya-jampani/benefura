import { BookOpen, MessageCircle, Plus, ScanLine } from "lucide-react";

import { HeroAmount, PillLink, Surface, Trend } from "@/components/ui";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { date, money } from "@/domain/describe";
import { addDays, type DateRange } from "@/domain/periods";
import type { MemberHeadline } from "@/domain/usage";
import type { Member, Plan } from "@/domain/types";

import { RELATIONSHIP_LABEL } from "./model";

const icon = "size-6";

export function HeroCard({
  plan,
  member,
  onMember,
  headline,
  scope,
  year,
}: {
  plan: Plan;
  member: Member;
  onMember: (id: string) => void;
  headline: MemberHeadline;
  scope: "used" | "all";
  year: DateRange;
}) {
  const c = plan.currency;
  const used = headline.usedBenefitIds.length;
  const who = RELATIONSHIP_LABEL[member.relationship];
  return (
    <Surface glow aria-labelledby="hero-title" className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="hero-title" className="text-lg text-ink-2">
          {scope === "used" ? "Left to claim this benefit year" : "Available this benefit year"}
        </h2>
        {plan.members.length > 1 && (
          <SegmentedControl
            size="sm"
            label="Whose benefits"
            value={member.id}
            onChange={onMember}
            segments={plan.members.map((m) => ({
              value: m.id,
              label: m.alias,
              ariaLabel: `${m.alias}, ${RELATIONSHIP_LABEL[m.relationship].toLowerCase()}`,
            }))}
          />
        )}
      </div>

      <p className="mt-8 sm:mt-10">
        <HeroAmount cents={headline.remainingCents} currency={c} />
      </p>

      <div className="mt-5 flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        {headline.paidCents > 0 ? (
          <Trend
            value={`${money(headline.paidCents, c)} paid back`}
            caption={`${who === "You" ? "to you" : `for ${member.alias}`} across ${used} benefit${used === 1 ? "" : "s"} since ${date(year.start, c)}`}
          />
        ) : (
          <Trend tone="muted" value="Nothing paid back yet" caption={`for ${member.alias} since ${date(year.start, c)}`} />
        )}
        <p className="text-base text-muted">Resets {date(addDays(year.end, 1), c)}</p>
      </div>

      <nav aria-label="Quick actions" className="no-print mt-8 grid grid-cols-4 gap-2 sm:mt-10 sm:gap-5">
        <PillLink href={`/claims?id=new&member=${member.id}`} label="New claim" icon={<Plus aria-hidden strokeWidth={1.5} className={icon} />} />
        <PillLink href={`/claims?id=new&member=${member.id}&receipt=1`} label="Add receipt" icon={<ScanLine aria-hidden strokeWidth={1.5} className={icon} />} />
        <PillLink href="/chat" label="Ask" icon={<MessageCircle aria-hidden strokeWidth={1.5} className={icon} />} />
        <PillLink href="/redact" label="Booklet" icon={<BookOpen aria-hidden strokeWidth={1.5} className={icon} />} />
      </nav>
    </Surface>
  );
}

