// The context is deliberately tiny and aliased; browser tools read everything else from IndexedDB on demand.
import type { AliasRecord } from "@/db/dexie";
import type { ChatContext, Plan, Region } from "@/domain/types";

export const CONTEXT_MAX_BYTES = 2048;
const MAX_MEMBER_ALIASES = 12;
const MAX_CATEGORIES = 40;
const MAX_NAME_CHARS = 80;
const MAX_CATEGORY_CHARS = 48;

export function byteLength(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`;
}

export interface ContextInput {
  plan: Plan | null;
  today: string;
  tz: string;
  region?: Region;
}

export function buildChatContext({ plan, today, tz, region = "CA" }: ContextInput): ChatContext {
  const planRegion = plan?.region ?? region;
  const context: ChatContext = {
    region: planRegion,
    today,
    tz: clip(tz, 64),
    currency: plan?.currency ?? (planRegion === "AU" ? "AUD" : "CAD"),
    planName: plan ? clip(plan.planName, MAX_NAME_CHARS) : null,
    memberAliases: (plan?.members ?? []).slice(0, MAX_MEMBER_ALIASES).map((m) => clip(m.alias, 32)),
    categories: (plan?.categories ?? []).slice(0, MAX_CATEGORIES).map((c) => clip(c.name, MAX_CATEGORY_CHARS)),
  };
  while (byteLength(context) > CONTEXT_MAX_BYTES && (context.categories?.length ?? 0) > 0) {
    context.categories = context.categories!.slice(0, -1);
  }
  while (byteLength(context) > CONTEXT_MAX_BYTES && (context.memberAliases?.length ?? 0) > 1) {
    context.memberAliases = context.memberAliases!.slice(0, -1);
  }
  return context;
}

const MIN_VALUE_CHARS = 2;
const DIGIT_SEPARATORS = /^[\d\s().+-]+$/;

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Numbers match however they are spaced or punctuated ("416 555 0142" = "416-555-0142").
function valuePattern(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (DIGIT_SEPARATORS.test(value) && digits.length >= 6) {
    return `\\(?${digits.split("").join("[\\s().-]*")}`;
  }
  return value.trim().split(/\s+/).map(escapeRegExp).join("\\s+");
}

function normalizeKey(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (DIGIT_SEPARATORS.test(value) && digits.length >= 6) return digits;
  return value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

// Longest value first and whole words only, so names and numbers the user hid never leave the browser.
export function applyAliases(text: string, aliases: Pick<AliasRecord, "token" | "values">[]): string {
  const byKey = new Map<string, string>();
  for (const alias of aliases) {
    for (const raw of alias.values) {
      const value = raw.trim();
      if (value.length < MIN_VALUE_CHARS) continue;
      const key = normalizeKey(value);
      if (!byKey.has(key)) byKey.set(key, alias.token);
    }
  }
  if (byKey.size === 0) return text;

  const values = [...new Set(aliases.flatMap((a) => a.values.map((v) => v.trim())))]
    .filter((v) => v.length >= MIN_VALUE_CHARS)
    .sort((a, b) => b.replace(/\s/g, "").length - a.replace(/\s/g, "").length);
  const pattern = new RegExp(`(?<![\\p{L}\\p{N}])(?:${values.map(valuePattern).join("|")})(?![\\p{L}\\p{N}])`, "giu");
  return text.replace(pattern, (match) => byKey.get(normalizeKey(match)) ?? match);
}
