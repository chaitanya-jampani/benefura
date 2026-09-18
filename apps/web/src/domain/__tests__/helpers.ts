import type { Claim, ClaimStatus, IsoDate } from "../types";

export interface LineSpec {
  benefitId: string;
  date: IsoDate;
  charged: number;
  quantity?: number;
  itemCode?: string | null;
  other?: number;
}

let seq = 0;

/** Bypasses validation so tests can set up any history. */
export function makeClaim(
  planId: string,
  memberId: string,
  status: ClaimStatus,
  lines: LineSpec[],
  paidCents?: number,
  id?: string,
): Claim {
  seq += 1;
  const claimId = id ?? `c${String(seq).padStart(5, "0")}`;
  const createdAt = new Date(Date.UTC(2020, 0, 1) + seq * 1000).toISOString();
  return {
    id: claimId,
    planId,
    status,
    patientMemberId: memberId,
    provider: null,
    lines: lines.map((l, i) => ({
      id: `${claimId}-l${i}`,
      serviceDate: l.date,
      benefitId: l.benefitId,
      itemCode: l.itemCode ?? null,
      description: null,
      quantity: l.quantity ?? 1,
      chargedCents: l.charged,
      otherPlanPaidCents: l.other ?? 0,
    })),
    outcome: paidCents === undefined ? null : { paidCents, decidedOn: null, note: null },
    attachments: [],
    history: [],
    deadline: null,
    createdAt,
    updatedAt: createdAt,
  };
}
