import { Bot, User } from "lucide-react";

import { Chip } from "@/components/ui";
import { date, STATUS_LABEL } from "@/domain/describe";
import type { ClaimEstimate } from "@/domain/estimate";
import { indexPlan } from "@/domain/ledger";
import type { Claim, ClaimStatus, Currency, Plan } from "@/domain/types";
import type { ReceiptRecord } from "@/db/dexie";
import { formatMoney } from "@/lib/format";

export const STATUS_TONE: Record<ClaimStatus, "muted" | "positive" | "caution" | "negative"> = {
  draft: "muted",
  submitted: "caution",
  paid: "positive",
  partially_paid: "positive",
  rejected: "negative",
};

export function StatusChip({ status }: { status: ClaimStatus }) {
  return <Chip tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Chip>;
}

function timestamp(iso: string, currency: Currency): string {
  return new Intl.DateTimeFormat(currency === "AUD" ? "en-AU" : "en-CA", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function ClaimHistory({ claim, currency }: { claim: Claim; currency: Currency }) {
  const entries = [...claim.history].reverse();
  return (
    <ol aria-label="Claim history" className="space-y-4">
      {entries.map((entry, i) => {
        const agent = entry.actor === "agent";
        const Icon = agent ? Bot : User;
        return (
          <li key={`${entry.at}-${i}`} className="flex gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-sunken text-ink-2">
              <Icon aria-hidden strokeWidth={1.5} className="size-4" />
            </span>
            <div className="min-w-0">
              <p className="text-base text-ink">{entry.note ?? STATUS_LABEL[entry.status]}</p>
              <p className="text-sm text-muted">
                {timestamp(entry.at, currency)}, by {agent ? `the assistant (${entry.agentName ?? "agent"}${entry.agentVersion ? ` version ${entry.agentVersion}` : ""})` : "you"}
              </p>
              {agent && entry.traceId && (
                <p className="mt-0.5 font-mono text-xs break-all text-muted">Trace {entry.traceId}</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function ClaimSummary({
  plan,
  claim,
  estimate,
  receipts,
}: {
  plan: Plan;
  claim: Claim;
  estimate: ClaimEstimate | null;
  receipts: ReceiptRecord[];
}) {
  const c = plan.currency;
  const index = indexPlan(plan);
  const member = plan.members.find((m) => m.id === claim.patientMemberId);
  const charged = claim.lines.reduce((s, l) => s + l.chargedCents, 0);
  return (
    <div className="space-y-6">
      <dl className="grid gap-x-8 gap-y-3 text-base sm:grid-cols-2">
        <div>
          <dt className="text-sm text-muted">Plan</dt>
          <dd className="text-ink">
            {plan.insurer}, {plan.planName}
          </dd>
        </div>
        <div>
          <dt className="text-sm text-muted">Patient</dt>
          <dd className="text-ink">{member?.alias ?? claim.patientMemberId}</dd>
        </div>
        <div>
          <dt className="text-sm text-muted">Provider</dt>
          <dd className="text-ink">{claim.provider ?? "Not entered"}</dd>
        </div>
        <div>
          <dt className="text-sm text-muted">Deadline</dt>
          <dd className="text-ink">{claim.deadline ? date(claim.deadline, c) : "None set by the plan"}</dd>
        </div>
      </dl>

      <ul aria-label="Claim lines" className="divide-y divide-line border-y border-line sm:hidden print:hidden">
        {claim.lines.map((line, i) => (
          <li key={line.id} className="flex items-start justify-between gap-4 py-3">
            <div className="min-w-0">
              <p className="text-base text-ink">{index.benefits.get(line.benefitId)?.name ?? line.benefitId}</p>
              <p className="text-sm text-muted">
                {[date(line.serviceDate, c), line.itemCode ? `item ${line.itemCode}` : null, line.quantity !== 1 ? `quantity ${line.quantity}` : null, line.otherPlanPaidCents > 0 ? `other plan paid ${formatMoney(line.otherPlanPaidCents, c)}` : null, line.description]
                  .filter(Boolean)
                  .join(", ")}
              </p>
            </div>
            <div className="shrink-0 text-right">
              <p className="text-base tabular-nums text-ink">{formatMoney(line.chargedCents, c)}</p>
              {estimate?.lines[i] && <p className="text-sm tabular-nums text-muted">estimate {formatMoney(estimate.lines[i].planPaysCents, c)}</p>}
            </div>
          </li>
        ))}
        <li className="flex justify-between gap-4 py-3 text-base font-medium">
          <span>Total</span>
          <span className="text-right tabular-nums">
            {formatMoney(charged, c)}
            {estimate && <span className="block text-sm font-normal text-muted">estimate {formatMoney(estimate.planPaysCents, c)}</span>}
          </span>
        </li>
      </ul>

      <div className="hidden sm:block print:block">
        <table className="w-full text-left text-base">
          <caption className="sr-only">Claim lines</caption>
          <thead>
            <tr className="border-b border-line text-sm text-muted">
              <th scope="col" className="py-2 pr-3 font-normal">Date</th>
              <th scope="col" className="py-2 pr-3 font-normal">Benefit</th>
              <th scope="col" className="py-2 pr-3 font-normal">Item</th>
              <th scope="col" className="py-2 pr-3 text-right font-normal">Qty</th>
              <th scope="col" className="py-2 pr-3 text-right font-normal">Charged</th>
              <th scope="col" className="py-2 text-right font-normal">Estimate</th>
            </tr>
          </thead>
          <tbody>
            {claim.lines.map((line, i) => (
              <tr key={line.id} className="border-b border-line align-top">
                <td className="py-2 pr-3 whitespace-nowrap">{date(line.serviceDate, c)}</td>
                <td className="py-2 pr-3">
                  {index.benefits.get(line.benefitId)?.name ?? line.benefitId}
                  {line.description && <span className="block text-sm text-muted">{line.description}</span>}
                </td>
                <td className="py-2 pr-3">{line.itemCode ?? ""}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{line.quantity}</td>
                <td className="py-2 pr-3 text-right tabular-nums">
                  {formatMoney(line.chargedCents, c)}
                  {line.otherPlanPaidCents > 0 && <span className="block text-sm text-muted">other plan {formatMoney(line.otherPlanPaidCents, c)}</span>}
                </td>
                <td className="py-2 text-right tabular-nums">{estimate?.lines[i] ? formatMoney(estimate.lines[i].planPaysCents, c) : ""}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="text-base">
              <th scope="row" colSpan={4} className="py-2 pr-3 text-left font-medium">
                Total
              </th>
              <td className="py-2 pr-3 text-right font-medium tabular-nums">{formatMoney(charged, c)}</td>
              <td className="py-2 text-right font-medium tabular-nums">{estimate ? formatMoney(estimate.planPaysCents, c) : ""}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {claim.outcome && (
        <p className="text-base text-ink">
          {claim.status === "rejected" ? "Rejected" : `Plan paid ${formatMoney(claim.outcome.paidCents, c)}`}
          {claim.outcome.decidedOn ? ` on ${date(claim.outcome.decidedOn, c)}` : ""}
          {claim.outcome.note ? `. ${claim.outcome.note}.` : "."}
        </p>
      )}
      {receipts.length > 0 && (
        <p className="text-base text-ink-2">
          Attached: {receipts.map((r) => `receipt from ${r.receipt?.providerName ?? "provider"}${r.source === "demo" ? " (demo)" : ""}`).join(", ")}.
        </p>
      )}
    </div>
  );
}
