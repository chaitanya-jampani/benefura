"use client";

import { ArrowLeft, Plus, Printer, ScanLine, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { DemoBanner } from "@/components/demo/DemoBanner";
import { ReceiptCapture } from "@/components/redaction/ReceiptCapture";
import { Button, Surface } from "@/components/ui";
import { Field, SelectInput, TextInput } from "@/components/ui/Field";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { db, type PlanRecord, type ReceiptRecord } from "@/db/dexie";
import { deleteClaim, saveClaim, useReceipts } from "@/db/hooks";
import { ClaimError, createDraftClaim, updateClaim, USER_ACTOR } from "@/domain/claims";
import { estimateClaim } from "@/domain/estimate";
import { indexPlan } from "@/domain/ledger";
import type { Claim } from "@/domain/types";

type ClaimAttachment = Claim["attachments"][number];
import { cn } from "@/lib/cn";

import { RELATIONSHIP_LABEL } from "@/components/dashboard/model";

import { ClaimHistory, ClaimSummary, StatusChip } from "./ClaimParts";
import { BenefitPicker } from "./BenefitPicker";
import { EstimateCard } from "./EstimateCard";
import { emptyLine, lineFromClaim, lineFromReceipt, lineProblems, toClaimLine, type LineDraft } from "./lineDraft";

export function ClaimEditor({
  record,
  claims,
  claim,
  includeSubmitted,
  today,
  initialMember,
  initialBenefit,
  openReceipt,
}: {
  record: PlanRecord;
  claims: Claim[];
  claim: Claim | null;
  includeSubmitted: boolean;
  today: string;
  initialMember?: string | null;
  initialBenefit?: string | null;
  openReceipt?: boolean;
}) {
  const plan = record.plan;
  const router = useRouter();
  const index = indexPlan(plan);
  const defaultMember = plan.members.find((m) => m.relationship === "self")?.id ?? plan.members[0]?.id ?? "";
  const [memberId, setMemberId] = useState(claim?.patientMemberId ?? (plan.members.some((m) => m.id === initialMember) ? initialMember! : defaultMember));
  const [provider, setProvider] = useState(claim?.provider ?? "");
  const [lines, setLines] = useState<LineDraft[]>(() =>
    claim && claim.lines.length ? claim.lines.map(lineFromClaim) : [emptyLine(today, initialBenefit && index.benefits.has(initialBenefit) ? initialBenefit : "")],
  );
  const [attachments, setAttachments] = useState<ClaimAttachment[]>(claim?.attachments ?? []);
  const [receiptOpen, setReceiptOpen] = useState(!!openReceipt);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showProblems, setShowProblems] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const receipts = useReceipts(attachments.map((a) => a.receiptId)) ?? [];

  const complete = lines.map(toClaimLine);
  const ready = complete.filter((l): l is NonNullable<typeof l> => l !== null);
  const estimate =
    ready.length > 0 && memberId
      ? estimateClaim(plan, claims, { patientMemberId: memberId, lines: ready }, { today, includeSubmitted, excludeClaimId: claim?.id })
      : null;

  const setLine = (key: string, patch: Partial<LineDraft>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  const onReceipt = (receipt: ReceiptRecord) => {
    setReceiptOpen(false);
    setAttachments((a) => (a.some((x) => x.receiptId === receipt.id) ? a : [...a, { receiptId: receipt.id, kind: "receipt" }]));
    const fields = receipt.receipt;
    if (!fields) {
      setNotice("The receipt was saved, but no details could be read. Enter them below.");
      return;
    }
    if (!provider && fields.providerName) setProvider(fields.providerName);
    const fromReceipt = fields.serviceLines.map((l) => lineFromReceipt(plan, l, fields.providerType, today));
    if (fromReceipt.length > 0) {
      setLines((ls) => {
        const untouched = ls.length === 1 && !ls[0].charged && !ls[0].description && !ls[0].itemCode;
        return untouched ? fromReceipt : [...ls, ...fromReceipt];
      });
    }
    const low = Object.entries(receipt.fieldConfidence ?? {}).filter(([, v]) => v.confidence < 0.8).length;
    setNotice(
      `Filled ${fromReceipt.length} line${fromReceipt.length === 1 ? "" : "s"} from the receipt.${low ? ` ${low} field${low === 1 ? " was" : "s were"} hard to read.` : ""} Check the benefit and amounts before saving.`,
    );
  };

  const persist = async (submit: boolean) => {
    setError(null);
    if (ready.length !== lines.length) {
      setShowProblems(true);
      setError("Some lines are incomplete. Check the highlighted fields.");
      return;
    }
    setBusy(true);
    try {
      const now = new Date();
      let next = claim
        ? updateClaim(plan, claim, { patientMemberId: memberId, provider, lines: ready.map((l) => ({ ...l, id: l.id ?? "" })), attachments }, USER_ACTOR, now)
        : { ...createDraftClaim(plan, { patientMemberId: memberId, provider, lines: ready }, USER_ACTOR, now), attachments };
      if (submit) next = updateClaim(plan, next, { status: "submitted" }, USER_ACTOR, now);
      await db.transaction("rw", [db.claims, db.receipts], async () => {
        await saveClaim(next);
        for (const a of attachments) await db.receipts.update(a.receiptId, { claimId: next.id });
      });
      if (!claim || submit) router.replace(`/claims?id=${next.id}`);
      else setNotice("Draft saved.");
    } catch (err) {
      setError(err instanceof ClaimError ? err.message : "The claim couldn't be saved in this browser.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!claim) return;
    await deleteClaim(claim.id);
    router.replace("/claims");
  };

  return (
    <div className="space-y-6">
      {record.isDemo && <DemoBanner region={plan.region} className="print:hidden" />}
      <div className="no-print flex flex-wrap items-center justify-between gap-3">
        <Link href="/claims" className="inline-flex h-10 items-center gap-2 rounded-full pr-4 text-base text-ink-2 hover:text-ink">
          <ArrowLeft aria-hidden strokeWidth={1.5} className="size-5" />
          All claims
        </Link>
        {claim && (
          <Button variant="ghost" size="sm" onClick={() => window.print()}>
            <Printer aria-hidden strokeWidth={1.5} className="size-4" />
            Print summary
          </Button>
        )}
      </div>

      <header className="flex flex-wrap items-center gap-3 px-1">
        <h1 className="text-3xl font-medium tracking-tight text-ink sm:text-4xl">{claim ? "Draft claim" : "New claim"}</h1>
        {claim && <StatusChip status={claim.status} />}
      </header>

      {claim && (
        <Surface className="hidden print:block print:p-0 print:shadow-none">
          <ClaimSummary plan={plan} claim={claim} estimate={estimate} receipts={receipts} />
        </Surface>
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 print:hidden lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          {receiptOpen ? (
            <Surface aria-labelledby="receipt-title" className="sm:p-8">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 id="receipt-title" className="text-xl font-medium text-ink">
                    Add from a receipt
                  </h2>
                  <p className="mt-1 text-base text-muted">
                    Names and numbers are covered in your browser before the image is read. You can always type the details instead.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setReceiptOpen(false)}
                  aria-label="Close receipt reader"
                  className="grid size-10 shrink-0 place-items-center rounded-full hover:bg-sunken"
                >
                  <X aria-hidden strokeWidth={1.5} className="size-5" />
                </button>
              </div>
              <div className="mt-6">
                <ReceiptCapture
                  region={plan.region}
                  planId={plan.id}
                  onAnalyzed={onReceipt}
                  onManual={() => setReceiptOpen(false)}
                  onCancel={() => setReceiptOpen(false)}
                />
              </div>
            </Surface>
          ) : (
            <div className="flex flex-wrap items-center gap-3 rounded-tile bg-surface/60 px-5 py-4">
              <p className="min-w-0 flex-1 text-base text-ink-2">Have a receipt? Benefura can read it and fill in the lines.</p>
              <Button variant="soft" size="sm" onClick={() => setReceiptOpen(true)}>
                <ScanLine aria-hidden strokeWidth={1.5} className="size-4" />
                Add from a receipt
              </Button>
            </div>
          )}

          {notice && (
            <p role="status" className="rounded-tile bg-surface/60 px-5 py-3 text-base text-ink-2">
              {notice}
            </p>
          )}

          <Surface aria-label="Claim details" className="sm:p-8">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void persist(false);
              }}
              className="space-y-6"
              noValidate
            >
              <div className="space-y-2">
                <p aria-hidden className="px-1 text-sm text-ink-2">
                  Who is the claim for
                </p>
                <SegmentedControl
                  label="Who is the claim for"
                  value={memberId}
                  onChange={setMemberId}
                  segments={plan.members.map((m) => ({ value: m.id, label: m.alias, ariaLabel: `${m.alias}, ${RELATIONSHIP_LABEL[m.relationship].toLowerCase()}` }))}
                />
              </div>

              <Field label="Provider" htmlFor="provider">
                <TextInput id="provider" value={provider} placeholder="Clinic or practitioner" onChange={(e) => setProvider(e.target.value)} autoComplete="off" />
              </Field>

              {lines.map((line, i) => (
                <LineFields
                  key={line.key}
                  n={i + 1}
                  line={line}
                  record={record}
                  showProblems={showProblems}
                  canRemove={lines.length > 1}
                  onChange={(patch) => setLine(line.key, patch)}
                  onRemove={() => setLines((ls) => ls.filter((l) => l.key !== line.key))}
                />
              ))}

              <Button
                variant="ghost"
                size="sm"
                onClick={() => setLines((ls) => [...ls, { ...emptyLine(ls.at(-1)?.serviceDate ?? today, ls.at(-1)?.benefitId ?? "") }])}
              >
                <Plus aria-hidden strokeWidth={1.5} className="size-4" />
                Add another line
              </Button>

              {error && (
                <p role="alert" className="text-base text-negative">
                  {error}
                </p>
              )}

              <div className="flex flex-wrap gap-3 border-t border-line pt-6">
                <Button type="submit" variant="primary" disabled={busy}>
                  Save draft
                </Button>
                <Button variant="soft" disabled={busy} onClick={() => persist(true)}>
                  Save and mark as submitted
                </Button>
                {claim &&
                  (confirmDelete ? (
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-base text-ink-2">Delete this draft?</span>
                      <Button variant="danger" size="sm" onClick={remove}>
                        Delete
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>
                        Keep it
                      </Button>
                    </span>
                  ) : (
                    <Button variant="ghost" onClick={() => setConfirmDelete(true)}>
                      <Trash2 aria-hidden strokeWidth={1.5} className="size-4" />
                      Delete
                    </Button>
                  ))}
              </div>
            </form>
          </Surface>

          {claim && (
            <Surface aria-labelledby="history-title" className="sm:p-8">
              <h2 id="history-title" className="text-xl font-medium text-ink">
                History
              </h2>
              <div className="mt-4">
                <ClaimHistory claim={claim} currency={plan.currency} />
              </div>
            </Surface>
          )}
        </div>

        <div className="lg:sticky lg:top-6">
          <EstimateCard
            plan={plan}
            estimate={estimate}
            benefitIds={ready.map((l) => l.benefitId)}
            incomplete={lines.length - ready.length}
            includeSubmitted={includeSubmitted}
            today={today}
          />
        </div>
      </div>
    </div>
  );
}

function LineFields({
  n,
  line,
  record,
  showProblems,
  canRemove,
  onChange,
  onRemove,
}: {
  n: number;
  line: LineDraft;
  record: PlanRecord;
  showProblems: boolean;
  canRemove: boolean;
  onChange: (patch: Partial<LineDraft>) => void;
  onRemove: () => void;
}) {
  const plan = record.plan;
  const benefit = line.benefitId ? indexPlan(plan).benefits.get(line.benefitId) : undefined;
  const schedule = benefit?.coverage.kind === "schedule" ? benefit.coverage.scheduleItems : [];
  const problems = showProblems ? lineProblems(line) : [];
  const id = (f: string) => `line-${line.key}-${f}`;
  const invalid = (p: string) => problems.includes(p as never);
  const ring = (p: string) => (invalid(p) ? "ring-negative/40" : undefined);
  return (
    <fieldset className="space-y-4 rounded-tile bg-sunken/60 p-4 sm:p-5">
      <legend className="sr-only">Line {n}</legend>
      <div className="flex min-h-9 items-center justify-between gap-3">
        <p aria-hidden className="text-base font-medium text-ink">
          Line {n}
        </p>
        {canRemove && (
          <button type="button" onClick={onRemove} className="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-sm text-ink-2 hover:bg-surface">
            <X aria-hidden strokeWidth={1.5} className="size-4" />
            Remove line {n}
          </button>
        )}
      </div>
      <Field label="Benefit" htmlFor={id("benefit")} hint={invalid("benefit") ? "Choose the benefit this service falls under." : undefined}>
        <BenefitPicker id={id("benefit")} plan={plan} value={line.benefitId} onChange={(benefitId) => onChange({ benefitId })} invalid={invalid("benefit")} />
      </Field>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Service date" htmlFor={id("date")}>
          <TextInput
            id={id("date")}
            type="date"
            value={line.serviceDate}
            max="9999-12-31"
            aria-invalid={invalid("date") || undefined}
            className={ring("date")}
            onChange={(e) => onChange({ serviceDate: e.target.value })}
          />
        </Field>
        {schedule.length > 0 ? (
          <Field label="Item number" htmlFor={id("item")} hint="The fund pays a set amount per item.">
            <SelectInput id={id("item")} value={line.itemCode} onChange={(e) => onChange({ itemCode: e.target.value })}>
              <option value="">Choose an item</option>
              {schedule.map((item) => (
                <option key={item.itemCode} value={item.itemCode}>
                  {item.itemCode}, {item.description}
                </option>
              ))}
              {line.itemCode && !schedule.some((i) => i.itemCode === line.itemCode) && <option value={line.itemCode}>{line.itemCode}, not on the schedule</option>}
            </SelectInput>
          </Field>
        ) : (
          <Field label="Item or procedure code" htmlFor={id("item")} optional>
            <TextInput id={id("item")} value={line.itemCode} onChange={(e) => onChange({ itemCode: e.target.value })} autoComplete="off" />
          </Field>
        )}
        <Field label="Description" htmlFor={id("description")} optional className="sm:col-span-2">
          <TextInput id={id("description")} value={line.description} onChange={(e) => onChange({ description: e.target.value })} autoComplete="off" />
        </Field>
        <Field label="Amount charged" htmlFor={id("charged")} hint={invalid("charged") ? "Enter an amount like 120 or 120.50." : undefined}>
          <MoneyInput id={id("charged")} value={line.charged} invalid={invalid("charged")} onChange={(charged) => onChange({ charged })} />
        </Field>
        <Field label={benefit?.coverage.kind === "per_diem" ? "Days" : "Quantity"} htmlFor={id("quantity")}>
          <TextInput
            id={id("quantity")}
            type="number"
            min={1}
            step="any"
            inputMode="decimal"
            value={line.quantity}
            aria-invalid={invalid("quantity") || undefined}
            className={ring("quantity")}
            onChange={(e) => onChange({ quantity: e.target.value })}
          />
        </Field>
        <Field label="Paid by another plan" htmlFor={id("other")} optional hint={invalid("other") ? "Can't be more than the amount charged." : undefined} className="sm:col-span-2">
          <MoneyInput id={id("other")} value={line.otherPaid} invalid={invalid("other")} onChange={(otherPaid) => onChange({ otherPaid })} />
        </Field>
      </div>
    </fieldset>
  );
}

function MoneyInput({ id, value, invalid, onChange }: { id: string; value: string; invalid?: boolean; onChange: (value: string) => void }) {
  return (
    <div className="relative">
      <span aria-hidden className="pointer-events-none absolute top-1/2 left-5 -translate-y-1/2 text-base text-muted">
        $
      </span>
      <TextInput
        id={id}
        inputMode="decimal"
        autoComplete="off"
        placeholder="0.00"
        value={value}
        aria-invalid={invalid || undefined}
        className={cn("pl-9 tabular-nums", invalid && "ring-negative/40")}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

