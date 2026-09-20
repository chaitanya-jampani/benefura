"use client";

import { ArrowLeft, Printer } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DemoBanner } from "@/components/demo/DemoBanner";
import { Button, HeroAmount, Surface } from "@/components/ui";
import { Field, TextInput } from "@/components/ui/Field";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import type { PlanRecord } from "@/db/dexie";
import { saveClaim, useReceipts } from "@/db/hooks";
import { ClaimError, isDecided, updateClaim, USER_ACTOR } from "@/domain/claims";
import { date, STATUS_LABEL } from "@/domain/describe";
import { estimateClaim } from "@/domain/estimate";
import { isIsoDate } from "@/domain/periods";
import type { Claim, Currency } from "@/domain/types";
import { formatMoney } from "@/lib/format";

import { ClaimHistory, ClaimSummary, StatusChip } from "./ClaimParts";
import { centsToInput, parseMoney } from "./lineDraft";

type Outcome = "paid" | "partially_paid" | "rejected";

export function ClaimDetail({
  record,
  claims,
  claim,
  includeSubmitted,
  today,
}: {
  record: PlanRecord;
  claims: Claim[];
  claim: Claim;
  includeSubmitted: boolean;
  today: string;
}) {
  const plan = record.plan;
  const c = plan.currency;
  const receipts = useReceipts(claim.attachments.map((a) => a.receiptId)) ?? [];
  const estimate = estimateClaim(plan, claims, claim, { today, includeSubmitted, excludeClaimId: claim.id });
  const charged = claim.lines.reduce((s, l) => s + l.chargedCents, 0);
  const decided = isDecided(claim.status);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const member = plan.members.find((m) => m.id === claim.patientMemberId);
  const title = claim.provider ?? "Claim";

  const move = async (status: "draft") => {
    setError(null);
    try {
      await saveClaim(updateClaim(plan, claim, { status }, USER_ACTOR, new Date()));
    } catch (err) {
      setError(err instanceof ClaimError ? err.message : "The claim couldn't be updated.");
    }
  };

  return (
    <div className="space-y-6">
      {record.isDemo && <DemoBanner region={plan.region} className="print:hidden" />}
      <div className="no-print flex flex-wrap items-center justify-between gap-3">
        <Link href="/claims" className="inline-flex h-10 items-center gap-2 rounded-full pr-4 text-base text-ink-2 hover:text-ink">
          <ArrowLeft aria-hidden strokeWidth={1.5} className="size-5" />
          All claims
        </Link>
        <Button variant="ghost" size="sm" onClick={() => window.print()}>
          <Printer aria-hidden strokeWidth={1.5} className="size-4" />
          Print summary
        </Button>
      </div>

      <header className="px-1">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-3xl font-medium tracking-tight text-ink sm:text-4xl">{title}</h1>
          <StatusChip status={claim.status} />
        </div>
        <p className="mt-1 text-base text-muted">
          For {member?.alias ?? "a member"}, {claim.lines.length} line{claim.lines.length === 1 ? "" : "s"}, {formatMoney(charged, c)} charged
        </p>
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="space-y-6 lg:col-start-1 lg:row-start-1">
          <Surface aria-label="Claim summary" className="sm:p-8 print:p-0 print:shadow-none">
            <h2 className="sr-only">Summary</h2>
            <ClaimSummary plan={plan} claim={claim} estimate={estimate} receipts={receipts} />
          </Surface>
          <Surface aria-labelledby="history-title" className="sm:p-8 print:hidden">
            <h2 id="history-title" className="text-xl font-medium text-ink">
              History
            </h2>
            <div className="mt-4">
              <ClaimHistory claim={claim} currency={c} />
            </div>
          </Surface>
        </div>

        <Surface glow as="aside" aria-labelledby="outcome-title" className="order-first sm:p-8 print:hidden lg:sticky lg:top-6 lg:order-none lg:col-start-2 lg:row-start-1">
          <h2 id="outcome-title" className="text-lg text-ink-2">
            {decided ? (claim.status === "rejected" ? "Rejected" : "Plan paid") : "Estimated payback"}
          </h2>
          <p className="mt-4">
            <HeroAmount
              cents={decided ? (claim.outcome?.paidCents ?? 0) : estimate.planPaysCents}
              currency={c}
              showCents
              className="[&_.text-hero]:text-[clamp(3rem,9vw,4.5rem)]"
            />
          </p>
          <p className="mt-3 text-base text-ink-2">
            {decided
              ? `The estimate was ${formatMoney(estimate.planPaysCents, c)} of ${formatMoney(charged, c)} charged.`
              : `Of ${formatMoney(charged, c)} charged. Counted toward your limits ${includeSubmitted ? "while the setting to count submitted claims is on" : "once it's paid"}.`}
          </p>

          {!decided || editing ? (
            <OutcomeForm
              key={claim.updatedAt}
              claim={claim}
              suggested={estimate.planPaysCents}
              currency={c}
              today={today}
              onDone={() => setEditing(false)}
              onSave={async (status, paidCents, decidedOn, note) => {
                await saveClaim(updateClaim(plan, claim, { status, outcome: { paidCents, decidedOn, note } }, USER_ACTOR, new Date()));
              }}
            />
          ) : (
            <Button variant="soft" size="sm" className="mt-6" onClick={() => setEditing(true)}>
              Correct the outcome
            </Button>
          )}

          {claim.status === "submitted" && (
            <div className="mt-6 border-t border-line pt-6">
              <p className="text-base text-ink-2">Need to change a line? Move it back to a draft first.</p>
              <Button variant="ghost" size="sm" className="mt-2 -ml-4" onClick={() => move("draft")}>
                Move back to draft
              </Button>
            </div>
          )}
          {claim.deadline && !decided && <p className="mt-4 text-sm text-muted">Deadline {date(claim.deadline, c)}.</p>}
          {error && (
            <p role="alert" className="mt-3 text-base text-negative">
              {error}
            </p>
          )}
        </Surface>
      </div>
    </div>
  );
}

function OutcomeForm({
  claim,
  suggested,
  currency,
  today,
  onSave,
  onDone,
}: {
  claim: Claim;
  suggested: number;
  currency: Currency;
  today: string;
  onSave: (status: Outcome, paidCents: number, decidedOn: string | null, note: string | null) => Promise<void>;
  onDone: () => void;
}) {
  const charged = claim.lines.reduce((s, l) => s + l.chargedCents, 0);
  const current = isDecided(claim.status) ? (claim.status as Outcome) : "paid";
  const [status, setStatus] = useState<Outcome>(current);
  const [amount, setAmount] = useState(centsToInput(claim.outcome?.paidCents ?? suggested));
  const [decidedOn, setDecidedOn] = useState(claim.outcome?.decidedOn ?? today);
  const [note, setNote] = useState(claim.outcome?.note ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const options: Outcome[] = ["paid", "partially_paid", "rejected"];

  const submit = async () => {
    setError(null);
    const paid = status === "rejected" ? 0 : parseMoney(amount);
    if (paid === null) return setError("Enter the amount the plan paid, like 80 or 80.50.");
    if (paid > charged) return setError(`The plan can't pay more than the ${formatMoney(charged, currency)} charged.`);
    if (decidedOn && !isIsoDate(decidedOn)) return setError("Enter the decision date.");
    setBusy(true);
    try {
      await onSave(status, paid, decidedOn || null, note.trim() || null);
      onDone();
    } catch (err) {
      setError(err instanceof ClaimError ? err.message : "The outcome couldn't be saved.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="mt-6 space-y-4 border-t border-line pt-6"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
      noValidate
    >
      <p className="text-base font-medium text-ink">{isDecided(claim.status) ? "Correct the outcome" : "Record the outcome"}</p>
      <SegmentedControl
        size="sm"
        label="Outcome"
        value={status}
        onChange={setStatus}
        segments={options.map((o) => ({ value: o, label: STATUS_LABEL[o] }))}
      />
      {status !== "rejected" && (
        <Field label="Amount the plan paid" htmlFor="paid-amount" hint={`The estimate was ${formatMoney(suggested, currency)}.`}>
          <div className="relative">
            <span aria-hidden className="pointer-events-none absolute top-1/2 left-5 -translate-y-1/2 text-base text-muted">
              $
            </span>
            <TextInput id="paid-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className="bg-surface/80 pl-9 tabular-nums" />
          </div>
        </Field>
      )}
      <Field label="Decided on" htmlFor="decided-on">
        <TextInput id="decided-on" type="date" value={decidedOn} onChange={(e) => setDecidedOn(e.target.value)} className="bg-surface/80" />
      </Field>
      <Field label="Note" htmlFor="outcome-note" hint="Optional, e.g. the reason on the statement.">
        <TextInput id="outcome-note" value={note} onChange={(e) => setNote(e.target.value)} className="bg-surface/80" />
      </Field>
      {error && (
        <p role="alert" className="text-base text-negative">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <Button type="submit" variant="primary" size="sm" disabled={busy}>
          Save outcome
        </Button>
        {isDecided(claim.status) && (
          <Button variant="ghost" size="sm" onClick={onDone}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
