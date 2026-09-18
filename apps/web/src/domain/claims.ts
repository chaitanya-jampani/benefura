// Claims only change through these functions so transitions, deadlines and history stay consistent
// whether the user or the assistant made the change.
import { indexPlan } from "./ledger";
import { claimDeadline, isIsoDate } from "./periods";
import type { Claim, ClaimHistoryEntry, ClaimLine, ClaimStatus, IsoDate, Plan } from "./types";

export type ClaimActor = Pick<ClaimHistoryEntry, "actor" | "agentName" | "agentVersion" | "traceId">;

export interface DraftClaimInput {
  patientMemberId: string;
  provider?: string | null;
  lines: Array<Omit<ClaimLine, "id">>;
}

export type ClaimPatch = Partial<Pick<Claim, "status" | "provider" | "lines" | "outcome" | "patientMemberId" | "attachments">>;

export class ClaimError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClaimError";
  }
}

export const USER_ACTOR: ClaimActor = { actor: "user", agentName: null, agentVersion: null, traceId: null };

const TRANSITIONS: Record<ClaimStatus, ClaimStatus[]> = {
  draft: ["submitted"],
  submitted: ["paid", "partially_paid", "rejected", "draft"],
  paid: ["partially_paid", "rejected"],
  partially_paid: ["paid", "rejected"],
  rejected: ["paid", "partially_paid"],
};

export function allowedTransitions(status: ClaimStatus): ClaimStatus[] {
  return TRANSITIONS[status];
}

export function canTransition(from: ClaimStatus, to: ClaimStatus): boolean {
  return from === to || TRANSITIONS[from].includes(to);
}

export function isDecided(status: ClaimStatus): boolean {
  return status === "paid" || status === "partially_paid" || status === "rejected";
}

function newId(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
      : Math.random().toString(36).slice(2, 18);
  return `${prefix}-${random}`;
}

function validateLines(plan: Plan, lines: Array<Omit<ClaimLine, "id"> & { id?: string }>): ClaimLine[] {
  const benefits = indexPlan(plan).benefits;
  return lines.map((line, i) => {
    const n = i + 1;
    if (!benefits.has(line.benefitId)) throw new ClaimError(`Line ${n}: choose a benefit from this plan.`);
    if (!isIsoDate(line.serviceDate)) throw new ClaimError(`Line ${n}: enter the service date.`);
    if (!Number.isInteger(line.chargedCents) || line.chargedCents < 0) throw new ClaimError(`Line ${n}: enter the amount charged.`);
    const other = line.otherPlanPaidCents ?? 0;
    if (!Number.isInteger(other) || other < 0) throw new ClaimError(`Line ${n}: the other plan's payment must be zero or more.`);
    if (other > line.chargedCents) throw new ClaimError(`Line ${n}: the other plan can't pay more than was charged.`);
    const quantity = line.quantity ?? 1;
    if (!(quantity > 0)) throw new ClaimError(`Line ${n}: quantity must be more than zero.`);
    return {
      id: line.id || newId("line"),
      serviceDate: line.serviceDate,
      benefitId: line.benefitId,
      itemCode: line.itemCode?.trim() || null,
      description: line.description?.trim() || null,
      quantity,
      chargedCents: line.chargedCents,
      otherPlanPaidCents: other,
    };
  });
}

function validateMember(plan: Plan, memberId: string) {
  if (!plan.members.some((m) => m.id === memberId)) throw new ClaimError("Choose who the claim is for.");
}

export function deadlineForLines(plan: Plan, lines: Pick<ClaimLine, "serviceDate">[]): IsoDate | null {
  const deadlines = lines.map((l) => claimDeadline(plan, l.serviceDate)).filter((d): d is IsoDate => d != null);
  return deadlines.length ? deadlines.sort()[0] : null;
}

function historyEntry(status: ClaimStatus, actor: ClaimActor, now: Date, note: string | null = null): ClaimHistoryEntry {
  return {
    status,
    at: now.toISOString(),
    actor: actor.actor,
    agentName: actor.agentName ?? null,
    agentVersion: actor.agentVersion ?? null,
    traceId: actor.traceId ?? null,
    note,
  };
}

export function createDraftClaim(plan: Plan, input: DraftClaimInput, actor: ClaimActor, now: Date): Claim {
  validateMember(plan, input.patientMemberId);
  const lines = validateLines(plan, input.lines);
  const at = now.toISOString();
  return {
    id: newId("clm"),
    planId: plan.id,
    status: "draft",
    patientMemberId: input.patientMemberId,
    provider: input.provider?.trim() || null,
    lines,
    outcome: null,
    attachments: [],
    history: [historyEntry("draft", actor, now, actor.actor === "agent" ? "Drafted by the assistant" : "Draft created")],
    deadline: deadlineForLines(plan, lines),
    createdAt: at,
    updatedAt: at,
  };
}

export function updateClaim(plan: Plan, claim: Claim, patch: ClaimPatch, actor: ClaimActor, now: Date): Claim {
  const status = patch.status ?? claim.status;
  if (!canTransition(claim.status, status)) {
    throw new ClaimError(`A ${claim.status.replace("_", " ")} claim can't move to ${status.replace("_", " ")}.`);
  }
  const editsContent = patch.lines !== undefined || patch.provider !== undefined || patch.patientMemberId !== undefined;
  if (editsContent && claim.status !== "draft" && status !== "draft") {
    throw new ClaimError("Move the claim back to draft before changing its details.");
  }

  const next: Claim = { ...claim, status };
  if (patch.patientMemberId !== undefined) {
    validateMember(plan, patch.patientMemberId);
    next.patientMemberId = patch.patientMemberId;
  }
  if (patch.provider !== undefined) next.provider = patch.provider?.trim() || null;
  if (patch.lines !== undefined) {
    next.lines = validateLines(plan, patch.lines);
    next.deadline = deadlineForLines(plan, next.lines);
  }
  if (patch.attachments !== undefined) next.attachments = patch.attachments;

  if (isDecided(status)) {
    const outcome = patch.outcome ?? claim.outcome;
    if (status === "rejected") {
      next.outcome = { paidCents: 0, decidedOn: outcome?.decidedOn ?? null, note: outcome?.note ?? null };
    } else {
      if (!outcome || !Number.isInteger(outcome.paidCents) || outcome.paidCents < 0) {
        throw new ClaimError("Enter the amount the plan paid.");
      }
      const charged = next.lines.reduce((s, l) => s + l.chargedCents, 0);
      if (outcome.paidCents > charged) throw new ClaimError("The plan can't pay more than was charged.");
      next.outcome = { paidCents: outcome.paidCents, decidedOn: outcome.decidedOn ?? null, note: outcome.note ?? null };
    }
  } else {
    if (patch.outcome) throw new ClaimError("Record an outcome only once the claim is paid, partially paid or rejected.");
    next.outcome = null;
  }

  if (status === "submitted" && claim.status !== "submitted" && next.lines.length === 0) throw new ClaimError("Add at least one line before submitting.");

  const statusChanged = status !== claim.status;
  if (statusChanged || actor.actor === "agent") {
    const note = statusChanged ? null : "Updated by the assistant";
    next.history = [...claim.history, historyEntry(status, actor, now, note)];
  }
  next.updatedAt = now.toISOString();
  return next;
}

export function claimTotals(claim: Claim): { chargedCents: number; paidCents: number | null } {
  return {
    chargedCents: claim.lines.reduce((s, l) => s + l.chargedCents, 0),
    paidCents: claim.outcome ? claim.outcome.paidCents : null,
  };
}

export const STATUS_ORDER: ClaimStatus[] = ["draft", "submitted", "partially_paid", "paid", "rejected"];

export function groupClaimsByStatus(claims: Claim[]): Array<{ status: ClaimStatus; claims: Claim[]; chargedCents: number; paidCents: number }> {
  return STATUS_ORDER.map((status) => {
    const group = claims
      .filter((c) => c.status === status)
      .sort((a, b) => (latestServiceDate(b) ?? "").localeCompare(latestServiceDate(a) ?? "") || b.updatedAt.localeCompare(a.updatedAt));
    return {
      status,
      claims: group,
      chargedCents: group.reduce((s, c) => s + claimTotals(c).chargedCents, 0),
      paidCents: group.reduce((s, c) => s + (claimTotals(c).paidCents ?? 0), 0),
    };
  }).filter((g) => g.claims.length > 0);
}

export function latestServiceDate(claim: Pick<Claim, "lines">): IsoDate | null {
  return claim.lines.reduce<IsoDate | null>((max, l) => (max === null || l.serviceDate > max ? l.serviceDate : max), null);
}
