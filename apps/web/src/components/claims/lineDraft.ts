import { findBenefits } from "@/domain/search";
import type { ClaimLine, IsoDate, Plan, ReceiptLine } from "@/domain/types";
import { isIsoDate } from "@/domain/periods";

export interface LineDraft {
  key: string;
  id?: string;
  serviceDate: string;
  benefitId: string;
  itemCode: string;
  description: string;
  quantity: string;
  charged: string;
  otherPaid: string;
}

let counter = 0;
export const newKey = () => `l${Date.now().toString(36)}${(counter++).toString(36)}`;

/** "$1,234.5" → 123450; null unless a non-negative amount with at most 2 decimals. */
export function parseMoney(input: string): number | null {
  const cleaned = input.replace(/[\s$, ]/g, "");
  if (cleaned === "") return null;
  if (!/^\d+(\.\d{0,2})?$/.test(cleaned)) return null;
  const [whole, frac = ""] = cleaned.split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"));
}

export function centsToInput(cents: number | null | undefined): string {
  if (cents == null) return "";
  return (cents / 100).toFixed(2);
}

export function emptyLine(today: IsoDate, benefitId = ""): LineDraft {
  return { key: newKey(), serviceDate: today, benefitId, itemCode: "", description: "", quantity: "1", charged: "", otherPaid: "" };
}

export function lineFromClaim(line: ClaimLine): LineDraft {
  return {
    key: newKey(),
    id: line.id,
    serviceDate: line.serviceDate,
    benefitId: line.benefitId,
    itemCode: line.itemCode ?? "",
    description: line.description ?? "",
    quantity: String(line.quantity),
    charged: centsToInput(line.chargedCents),
    otherPaid: line.otherPlanPaidCents ? centsToInput(line.otherPlanPaidCents) : "",
  };
}

export function lineFromReceipt(plan: Plan, line: ReceiptLine, providerType: string | null, today: IsoDate): LineDraft {
  const guess =
    (line.itemCode && findBenefits(plan, line.itemCode, 1)[0]) ||
    (line.description && findBenefits(plan, line.description, 1)[0]) ||
    (providerType && findBenefits(plan, providerType, 1)[0]) ||
    null;
  return {
    key: newKey(),
    serviceDate: line.serviceDate && isIsoDate(line.serviceDate) ? line.serviceDate : today,
    benefitId: guess?.benefitId ?? "",
    itemCode: line.itemCode ?? "",
    description: line.description ?? "",
    quantity: line.quantity && line.quantity > 0 ? String(line.quantity) : "1",
    charged: centsToInput(line.amountCents),
    otherPaid: "",
  };
}

export type LineProblem = "benefit" | "date" | "charged" | "quantity" | "other";

export function lineProblems(line: LineDraft): LineProblem[] {
  const problems: LineProblem[] = [];
  if (!line.benefitId) problems.push("benefit");
  if (!isIsoDate(line.serviceDate)) problems.push("date");
  const charged = parseMoney(line.charged);
  if (charged === null) problems.push("charged");
  const quantity = Number(line.quantity);
  if (!(quantity > 0)) problems.push("quantity");
  const other = line.otherPaid.trim() === "" ? 0 : parseMoney(line.otherPaid);
  if (other === null || (charged !== null && other > charged)) problems.push("other");
  return problems;
}

export function toClaimLine(line: LineDraft): (Omit<ClaimLine, "id"> & { id?: string }) | null {
  if (lineProblems(line).length > 0) return null;
  return {
    id: line.id,
    serviceDate: line.serviceDate,
    benefitId: line.benefitId,
    itemCode: line.itemCode.trim() || null,
    description: line.description.trim() || null,
    quantity: Number(line.quantity),
    chargedCents: parseMoney(line.charged)!,
    otherPlanPaidCents: line.otherPaid.trim() === "" ? 0 : parseMoney(line.otherPaid)!,
  };
}
