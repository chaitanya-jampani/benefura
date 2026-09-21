import type { AliasKind } from "@/db/dexie";

import type { Detection, DetectionSignals, TokenFamily } from "../types";
import type { LabelHit } from "./labels";
import { labelBefore } from "./labels";
import { scoreSignals } from "./score";
import type { TextStream } from "./text-stream";

export interface PatternContext {
  pageIndex: number;
  stream: TextStream;
  labels: LabelHit[];
}

export interface PatternSpec {
  detector: string;
  label: string;
  kind: AliasKind;
  family: TokenFamily | null;
  /** Named group `v`, if present, is the part to box. */
  regex: RegExp;
  /** null drops the match. */
  check: (value: string, match: RegExpExecArray, ctx: PatternContext) => Omit<DetectionSignals, "label"> | null;
  labelIds?: readonly string[];
  requireLabel?: boolean;
  requireChecksumOrLabel?: boolean;
  base?: number;
  defaultEnabled?: (value: string, signals: DetectionSignals) => boolean;
  describe?: (value: string, signals: DetectionSignals) => string;
}

export function withFlags(re: RegExp, flags: string): RegExp {
  const merged = new Set([...re.flags, ...flags]);
  return new RegExp(re.source, [...merged].join(""));
}

export function runPattern(spec: PatternSpec, ctx: PatternContext): Detection[] {
  const out: Detection[] = [];
  const re = withFlags(spec.regex, "dg");
  let m: RegExpExecArray | null;
  while ((m = re.exec(ctx.stream.text))) {
    if (m[0].length === 0) {
      re.lastIndex++;
      continue;
    }
    const [start, end] = m.indices?.groups?.v ?? m.indices?.[0] ?? [m.index, m.index + m[0].length];
    const value = ctx.stream.text.slice(start, end);
    const partial = spec.check(value, m, ctx);
    if (!partial) continue;
    const label = labelBefore(ctx.labels, ctx.stream.text, start, spec.labelIds);
    const signals: DetectionSignals = { ...partial, label };
    if (spec.requireLabel && !label) continue;
    if (spec.requireChecksumOrLabel && !signals.checksum && !label) continue;
    out.push({
      pageIndex: ctx.pageIndex,
      start,
      end,
      value,
      kind: spec.kind,
      family: spec.family,
      detector: spec.detector,
      label: spec.describe?.(value, signals) ?? spec.label,
      confidence: scoreSignals(signals, spec.base),
      signals,
      defaultEnabled: spec.defaultEnabled?.(value, signals) ?? true,
    });
  }
  return out;
}

// Shared inbox local parts are usually the insurer's or clinic's, not the member's.
const ROLE_MAILBOX =
  /^(claims?|info|support|service|services|help|contact|customer[\w.-]*|members?|member[\w.-]*|enquiries|inquiries|hello|admin|office|reception|noreply|no-reply|benefits|group[\w.-]*|privacy|feedback|billing|accounts?)$/i;

export const EMAIL: PatternSpec = {
  detector: "email",
  label: "Email address",
  kind: "email",
  family: "EMAIL",
  regex: /(?<![\w.+-])(?<v>[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24})(?![\w-])/g,
  check: () => ({}),
  labelIds: ["email"],
  base: 0.8,
  defaultEnabled: (value, signals) => signals.label === true || !ROLE_MAILBOX.test(value.split("@")[0]),
  describe: (value, signals) =>
    ROLE_MAILBOX.test(value.split("@")[0]) && !signals.label ? "Email address (shared inbox)" : "Email address",
};

export function detectCommon(ctx: PatternContext): Detection[] {
  return runPattern(EMAIL, ctx);
}
