import type { AliasKind, AliasRecord } from "@/db/dexie";

import { AliasBook, formatToken, normalizeAliasValue, parseToken } from "./apply";
import type { KnownValue, TokenFamily } from "./types";

export type FamilyRelationship = "spouse" | "child" | "dependent";

export interface FamilyMember {
  id: string;
  name: string;
  relationship: FamilyRelationship;
}

export interface HideFormValues {
  selfName: string;
  family: FamilyMember[];
  employer: string;
  policyNumber: string;
  certificateNumber: string;
  address: string;
  phone: string;
  email: string;
}

export type SingleField = Exclude<keyof HideFormValues, "family">;

export const EMPTY_FORM: HideFormValues = {
  selfName: "",
  family: [],
  employer: "",
  policyNumber: "",
  certificateNumber: "",
  address: "",
  phone: "",
  email: "",
};

export const FIELD_META: Record<SingleField, { family: TokenFamily; kind: AliasKind }> = {
  selfName: { family: "MEMBER", kind: "member" },
  employer: { family: "EMPLOYER", kind: "employer" },
  policyNumber: { family: "POLICY", kind: "policy" },
  certificateNumber: { family: "CERT", kind: "policy" },
  address: { family: "ADDRESS", kind: "address" },
  phone: { family: "PHONE", kind: "phone" },
  email: { family: "EMAIL", kind: "email" },
};

export interface FormTokens {
  fields: Record<SingleField, string>;
  family: Record<string, string>;
}

function isFree(book: AliasBook, token: string): boolean {
  const record = book.get(token);
  return !record || record.values.length === 0;
}

/** Mutates `book`. Empty fields still get a preview token so the form can show what a value would become. */
export function applyFormToBook(form: HideFormValues, book: AliasBook): { known: KnownValue[]; tokens: FormTokens } {
  const known: KnownValue[] = [];
  const reserved = new Set<string>();

  const pick = (family: TokenFamily, value: string, preferred: number): string => {
    const existing = value.trim() ? book.lookup(value) : undefined;
    if (existing) return existing;
    for (let i = preferred; ; i++) {
      const token = formatToken(family, i);
      if (isFree(book, token) && !reserved.has(token)) return token;
    }
  };

  const fields = {} as Record<SingleField, string>;
  for (const key of Object.keys(FIELD_META) as SingleField[]) {
    const { family, kind } = FIELD_META[key];
    const value = form[key].trim();
    const token = pick(family, value, 0);
    fields[key] = token;
    reserved.add(token);
    if (value) {
      book.assign(token, kind, value, key === "selfName" ? "self" : undefined);
      known.push({ token, kind, value, isName: key === "selfName" });
    }
  }

  const family: Record<string, string> = {};
  for (const member of form.family) {
    const value = member.name.trim();
    const token = pick("MEMBER", value, 1);
    family[member.id] = token;
    reserved.add(token);
    if (value) {
      book.assign(token, "member", value, member.relationship);
      known.push({ token, kind: "member", value, isName: true });
    }
  }

  // Values saved from earlier documents are hidden too, even if they're not on the form.
  const key = (k: KnownValue) => `${k.token}|${normalizeAliasValue(k.value)}`;
  const typed = new Set(known.map(key));
  for (const k of book.knownValues()) {
    if (!typed.has(key(k))) known.push(k);
  }
  return { known, tokens: { fields, family } };
}

export function formFromAliases(records: readonly AliasRecord[], newId: () => string): HideFormValues {
  const form: HideFormValues = { ...EMPTY_FORM, family: [] };
  const first = (predicate: (r: AliasRecord) => boolean) => records.find((r) => predicate(r) && r.values.length > 0)?.values[0] ?? "";
  const family = (token: string) => parseToken(token)?.family;

  form.selfName = first((r) => r.kind === "member" && r.relationship === "self");
  form.employer = first((r) => family(r.token) === "EMPLOYER");
  form.policyNumber = first((r) => family(r.token) === "POLICY");
  form.certificateNumber = first((r) => family(r.token) === "CERT");
  form.address = first((r) => family(r.token) === "ADDRESS");
  form.phone = first((r) => family(r.token) === "PHONE");
  form.email = first((r) => family(r.token) === "EMAIL");
  form.family = records
    .filter((r) => r.kind === "member" && r.relationship && r.relationship !== "self" && r.values.length > 0)
    .sort((a, b) => a.token.localeCompare(b.token))
    .map((r) => ({ id: newId(), name: r.values[0], relationship: r.relationship as FamilyRelationship }));
  return form;
}
