import { TriangleAlert } from "lucide-react";

import { AskLink } from "@/components/dashboard/AskLink";
import { daysLeftText } from "@/components/dashboard/model";
import { Chip, HeroAmount, Surface } from "@/components/ui";
import { date, money } from "@/domain/describe";
import type { ClaimEstimate, EstimateLimiter } from "@/domain/estimate";
import { indexPlan } from "@/domain/ledger";
import { daysBetween } from "@/domain/periods";
import type { Plan } from "@/domain/types";
import { formatMoney } from "@/lib/format";

const LIMITED_BY: Record<Exclude<EstimateLimiter, null>, string> = {
  coverage: "the coverage rate",
  per_service_cap: "the per-visit cap",
  schedule: "the item schedule",
  other_plan: "the other plan's payment",
  limit: "the benefit maximum",
  pool: "the shared maximum",
  frequency: "how often it's covered",
  waiting_period: "the waiting period",
  deductible: "the deductible",
  not_covered: "coverage rules",
};

export function EstimateCard({
  plan,
  estimate,
  benefitIds,
  incomplete,
  includeSubmitted,
  today,
  title = "Estimated payback",
}: {
  plan: Plan;
  estimate: ClaimEstimate | null;
  benefitIds: string[];
  incomplete: number;
  includeSubmitted: boolean;
  today: string;
  title?: string;
}) {
  const c = plan.currency;
  const index = indexPlan(plan);
  const hasLines = estimate && estimate.lines.length > 0;
  return (
    <Surface glow as="aside" aria-labelledby="estimate-title" aria-live="polite" className="sm:p-8">
      <h2 id="estimate-title" className="text-lg text-ink-2">
        {title}
      </h2>
      <p className="mt-4">
        <HeroAmount cents={estimate?.planPaysCents ?? 0} currency={c} showCents className="[&_.text-hero]:text-[clamp(3rem,9vw,4.5rem)]" />
      </p>
      {hasLines ? (
        <p className="mt-3 text-base text-ink-2">
          You pay {formatMoney(estimate.memberPaysCents, c)} of {formatMoney(estimate.chargedCents, c)} charged.
        </p>
      ) : (
        <p className="mt-3 text-base text-muted">Add a benefit, date and amount to see what the plan pays.</p>
      )}
      {incomplete > 0 && hasLines && (
        <p className="mt-1 text-sm text-muted">
          {incomplete} line{incomplete === 1 ? " isn't" : "s aren't"} complete yet and {incomplete === 1 ? "isn't" : "aren't"} included.
        </p>
      )}
      {estimate?.deadline && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Chip tone={daysBetween(today, estimate.deadline) <= 30 ? "caution" : "muted"}>
            Send by {date(estimate.deadline, c)}, {daysLeftText(daysBetween(today, estimate.deadline)).toLowerCase()}
          </Chip>
        </div>
      )}

      {hasLines &&
        estimate.lines.map((line, i) => {
          const benefit = index.benefits.get(benefitIds[i] ?? line.benefitId);
          return (
            <section key={i} aria-label={`Line ${i + 1}`} className="mt-6 rounded-tile bg-surface/80 p-4 shadow-tile">
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="min-w-0 truncate text-base font-medium text-ink">{benefit?.name ?? "Benefit"}</h3>
                <p className="shrink-0 text-base font-medium tabular-nums">{formatMoney(line.planPaysCents, c)}</p>
              </div>
              <ul className="mt-3 space-y-2">
                {line.steps.map((step, s) => (
                  <li key={s} className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="min-w-0 text-ink-2">
                      {step.label}
                      {step.detail && <span className="block text-muted">{step.detail}</span>}
                    </span>
                    <span className="shrink-0 text-muted tabular-nums">{money(step.planPaysCents, c)}</span>
                  </li>
                ))}
              </ul>
              {line.limitedBy && line.limitedBy !== "coverage" && !(line.limitedBy === "schedule" && line.planPaysCents > 0) && (
                <p className="mt-3 text-sm text-ink-2">Limited by {LIMITED_BY[line.limitedBy]}.</p>
              )}
              {(line.remainingAfter.limitCents != null || line.remainingAfter.poolCents != null) && (
                <p className="mt-1 text-sm text-muted">
                  After this:{" "}
                  {[
                    line.remainingAfter.limitCents != null ? `${money(line.remainingAfter.limitCents, c)} left of the maximum` : null,
                    line.remainingAfter.poolCents != null ? `${money(line.remainingAfter.poolCents, c)} left in the shared maximum` : null,
                  ]
                    .filter(Boolean)
                    .join(", ")}
                  .
                </p>
              )}
              {line.warnings.length > 0 && (
                <ul className="mt-3 space-y-1">
                  {line.warnings.map((w) => (
                    <li key={w} className="flex gap-2 text-sm text-caution">
                      <TriangleAlert aria-hidden strokeWidth={1.5} className="mt-0.5 size-4 shrink-0" />
                      <span>{w}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}

      <p className="mt-6 text-sm text-muted">
        Calculated in your browser from your plan and {includeSubmitted ? "paid and submitted" : "paid"} claims. An estimate, not a
        promise from your insurer.
      </p>
      {hasLines && (
        <AskLink className="mt-3 -ml-1" question={`Explain this estimate: the plan pays ${formatMoney(estimate.planPaysCents, c)} of ${formatMoney(estimate.chargedCents, c)}.`} label="Ask about this estimate" />
      )}
    </Surface>
  );
}
