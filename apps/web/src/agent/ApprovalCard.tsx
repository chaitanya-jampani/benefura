"use client";

import { useLiveQuery } from "dexie-react-hooks";
import { Check, CircleAlert, FilePen, FilePlus, X } from "lucide-react";
import Link from "next/link";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui";
import type { AnyToolPart } from "@/components/chat/ToolCard";
import { db } from "@/db/dexie";
import type { Currency, Plan } from "@/domain/types";
import { cn } from "@/lib/cn";
import { formatDate, formatMoney } from "@/lib/format";

import type { ToolInputs } from "./types";

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  submitted: "Submitted",
  paid: "Paid",
  partially_paid: "Partially paid",
  rejected: "Rejected",
};

function benefitName(plan: Plan | null, benefitId: string): string {
  const benefit = plan?.categories.flatMap((c) => c.benefits).find((b) => b.id === benefitId);
  return benefit?.name ?? benefitId;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right text-ink">{children}</dd>
    </div>
  );
}

function DraftDetails({ input, plan, currency }: { input: ToolInputs["draft_claim"]; plan: Plan | null; currency: Currency }) {
  const total = input.lines.reduce((sum, l) => sum + (l.charged_cents ?? 0), 0);
  return (
    <dl className="divide-y divide-line">
      <Row label="Patient">{input.member_alias}</Row>
      {input.provider && <Row label="Provider">{input.provider}</Row>}
      {input.lines.map((line, i) => (
        <Row key={i} label={`${benefitName(plan, line.benefit_id)}, ${line.service_date ? formatDate(line.service_date, currency) : "no date"}`}>
          <span className="tabular-nums">{formatMoney(line.charged_cents ?? 0, currency)}</span>
        </Row>
      ))}
      {input.lines.length > 1 && (
        <Row label="Total">
          <span className="font-medium tabular-nums">{formatMoney(total, currency)}</span>
        </Row>
      )}
    </dl>
  );
}

function UpdateDetails({ input, plan, currency }: { input: ToolInputs["update_claim"]; plan: Plan | null; currency: Currency }) {
  const claim = useLiveQuery(() => (input.claim_id ? db.claims.get(input.claim_id) : undefined), [input.claim_id]);
  const firstLine = claim?.lines[0];
  return (
    <dl className="divide-y divide-line">
      <Row label="Claim">
        {firstLine
          ? `${benefitName(plan, firstLine.benefitId)}, ${formatDate(firstLine.serviceDate, currency)}`
          : input.claim_id}
      </Row>
      {input.status && <Row label="New status">{STATUS_LABELS[input.status] ?? input.status}</Row>}
      {input.paid_cents !== null && input.paid_cents !== undefined && (
        <Row label="Amount paid">
          <span className="tabular-nums">{formatMoney(input.paid_cents, currency)}</span>
        </Row>
      )}
      {input.provider && <Row label="Provider">{input.provider}</Row>}
      {input.note && <Row label="Note">{input.note}</Row>}
    </dl>
  );
}

export interface ApprovalCardProps {
  part: AnyToolPart;
  plan: Plan | null;
  active: boolean;
  busy: boolean;
  onApprove: () => void;
  onDecline: (reason: string) => void;
}

export function ApprovalCard({ part, plan, active, busy, onApprove, onDecline }: ApprovalCardProps) {
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const titleId = useId();
  const isDraft = part.type === "tool-draft_claim";
  const currency: Currency = plan?.currency ?? "CAD";
  const Icon = isDraft ? FilePlus : FilePen;

  const approval = "approval" in part ? part.approval : undefined;
  const pending = part.state === "approval-requested";
  const declined = part.state === "output-denied" || (part.state === "approval-responded" && approval?.approved === false);
  const saved = part.state === "output-available";
  const failed = part.state === "output-error";
  const saving = busy || (part.state === "approval-responded" && approval?.approved === true);

  const title = isDraft ? "Add this draft claim?" : "Update this claim?";

  return (
    <section
      aria-labelledby={titleId}
      className="flex w-full max-w-xl flex-col gap-4 rounded-tile bg-surface p-5 shadow-tile ring-1 ring-line"
      data-testid="approval-card"
      data-state={part.state}
    >
      <header className="flex items-center gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-sunken text-ink">
          <Icon aria-hidden className="size-5" strokeWidth={1.5} />
        </span>
        <div className="flex min-w-0 flex-col">
          <h3 id={titleId} className="text-lg font-medium text-ink">
            {title}
          </h3>
          <p className="text-sm text-muted">The assistant needs your approval to change your records.</p>
        </div>
      </header>

      {part.input ? (
        isDraft ? (
          <DraftDetails input={part.input as ToolInputs["draft_claim"]} plan={plan} currency={currency} />
        ) : (
          <UpdateDetails input={part.input as ToolInputs["update_claim"]} plan={plan} currency={currency} />
        )
      ) : null}

      {pending && active && !declining && (
        <div className="flex flex-wrap gap-3">
          <Button variant="primary" size="md" onClick={onApprove} disabled={busy}>
            <Check aria-hidden className="size-5" strokeWidth={1.5} />
            Approve
          </Button>
          <Button
            variant="soft"
            size="md"
            disabled={busy}
            onClick={() => {
              setDeclining(true);
              requestAnimationFrame(() => reasonRef.current?.focus());
            }}
          >
            <X aria-hidden className="size-5" strokeWidth={1.5} />
            Decline
          </Button>
        </div>
      )}

      {pending && active && declining && (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            onDecline(reason.trim());
          }}
        >
          <label className="flex flex-col gap-2 text-sm text-ink-2">
            What should change? (optional)
            <textarea
              ref={reasonRef}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              maxLength={300}
              className="resize-none rounded-tile bg-sunken px-4 py-3 text-base text-ink outline-none placeholder:text-faint focus-visible:ring-2 focus-visible:ring-focus"
              placeholder="For example, the amount was $90"
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <Button type="submit" variant="primary" size="md">
              Decline
            </Button>
            <Button variant="ghost" size="md" onClick={() => setDeclining(false)}>
              Back
            </Button>
          </div>
        </form>
      )}

      {pending && !active && <p className="text-sm text-muted">No longer waiting for an answer.</p>}

      <p
        role="status"
        className={cn("flex items-center gap-2 text-sm", declined || failed ? "text-ink-2" : "text-positive", !(declined || saved || failed || saving) && "sr-only")}
      >
        {saving && !saved && !failed && "Approved. Saving…"}
        {saved && (
          <>
            <Check aria-hidden className="size-4" strokeWidth={1.5} />
            <span>
              Approved and saved.{" "}
              <Link href="/claims" className="rounded-sm text-ink underline decoration-faint underline-offset-4 hover:decoration-ink">
                View claims
              </Link>
            </span>
          </>
        )}
        {failed && (
          <>
            <CircleAlert aria-hidden className="size-4 text-caution" strokeWidth={1.5} />
            <span>Approved, but it couldn&apos;t be saved: {"errorText" in part ? part.errorText : ""}</span>
          </>
        )}
        {declined && <span>Declined{approval?.reason ? `: ${approval.reason}` : ""}</span>}
      </p>
    </section>
  );
}
