import type { PiiDetectedDetail } from "@/domain/types";

// The server reports category names only, never values.
const PHRASES: Record<string, string> = {
  CASocialInsuranceNumber: "a social insurance number",
  CAHealthServiceNumber: "a health card number",
  CAPersonalHealthIdentification: "a personal health number",
  CABankAccountNumber: "a bank account number",
  AUTaxFileNumber: "a tax file number",
  AUMedicalAccountNumber: "a Medicare number",
  AUBankAccountNumber: "a bank account number",
  AUDriversLicenseNumber: "a driver's licence number",
  DateOfBirth: "a date of birth",
  Person: "a person's name",
  Organization: "an organization's name",
  Address: "an address",
  PhoneNumber: "a phone number",
  Email: "an email address",
};

export function categoryPhrase(category: string | null | undefined): string {
  if (!category) return "personal details";
  return PHRASES[category] ?? "personal details";
}

/** `fix` is a 1-based page; the link carries coordinates, never values. */
export function fixHref(documentId: string, detail: Pick<PiiDetectedDetail, "page" | "category" | "polygon">, fallbackPage: number): string {
  const params = new URLSearchParams({ doc: documentId, fix: String(detail.page ?? fallbackPage) });
  if (detail.category) params.set("category", detail.category);
  if (detail.polygon?.length) params.set("polygon", detail.polygon.map((n) => Number(n.toFixed(4))).join(","));
  return `/redact?${params.toString()}`;
}

export function parsePolygon(value: string | null): number[] | null {
  if (!value) return null;
  const nums = value.split(",").map(Number);
  return nums.length >= 4 && nums.every(Number.isFinite) ? nums : null;
}
