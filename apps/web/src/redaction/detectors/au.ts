import type { Detection } from "../types";
import { abnValid, auPostcodeMatchesState, ihiValid, medicareValid, tfnValid } from "./checksums";
import { runPattern, type PatternContext, type PatternSpec } from "./common";

const BUSINESS_LINE = /^(?:1300|1800|13\d{2})/;

export const AU_PATTERNS: readonly PatternSpec[] = [
  {
    detector: "au.tfn",
    label: "Tax file number",
    kind: "identifier",
    family: "ID",
    regex: /(?<![\d-])(?<v>\d{3}[ -]?\d{3}[ -]?\d{2,3})(?![\d-])/g,
    check: (value) => ({ checksum: tfnValid(value), region: true }),
    labelIds: ["health", "health_acronym"],
    requireChecksumOrLabel: true,
    base: 0.3,
  },
  {
    detector: "au.medicare",
    label: "Medicare card number",
    kind: "identifier",
    family: "ID",
    regex: /(?<![\d-])(?<v>[2-6]\d{3}[ -]?\d{5}[ -]?\d(?:[ /-]?[1-9])?)(?![\d-])/g,
    check: (value) => ({ checksum: medicareValid(value), region: true }),
    labelIds: ["health", "health_acronym", "certificate"],
    requireChecksumOrLabel: true,
    base: 0.3,
  },
  {
    detector: "au.ihi",
    label: "Individual healthcare identifier",
    kind: "identifier",
    family: "ID",
    regex: /(?<![\d-])(?<v>8003[ -]?60\d{2}[ -]?\d{4}[ -]?\d{4})(?![\d-])/g,
    check: (value) => (ihiValid(value) ? { checksum: true, region: true } : null),
    labelIds: ["health", "health_acronym"],
    base: 0.35,
  },
  {
    detector: "au.abn",
    label: "Australian business number",
    kind: "identifier",
    family: "ID",
    regex: /(?<![\d-])(?<v>\d{2}[ ]?\d{3}[ ]?\d{3}[ ]?\d{3})(?![\d-])/g,
    check: (value, match, ctx) => {
      if (!abnValid(value)) return null;
      return { checksum: true, region: /ABN[\s:.]*$/.test(ctx.stream.text.slice(Math.max(0, match.index - 8), match.index)) };
    },
    base: 0.3,
    // An ABN on a policy or receipt identifies the insurer or the provider, not the member.
    defaultEnabled: () => false,
    describe: () => "Business number (usually the insurer's or provider's)",
  },
  {
    detector: "au.bsb",
    label: "BSB",
    kind: "identifier",
    family: null,
    regex: /(?<![\d-])(?<v>\d{3}[ -]?\d{3})(?![\d-])/g,
    check: () => ({ region: true }),
    labelIds: ["bank"],
    requireLabel: true,
    base: 0.55,
  },
  {
    detector: "au.postcode",
    label: "Postcode",
    kind: "address",
    family: null,
    regex: /(?<![\p{L}\p{N}])(?:NSW|VIC|QLD|SA|WA|TAS|NT|ACT)[ ,]+(?<v>\d{4})(?![\p{L}\p{N}])/gu,
    check: (value, match) => {
      const state = /^[A-Z]+/.exec(match[0])?.[0] ?? "";
      return { region: auPostcodeMatchesState(state, value) };
    },
    labelIds: ["address"],
    base: 0.55,
  },
  {
    detector: "au.phone",
    label: "Phone number",
    kind: "phone",
    family: "PHONE",
    regex:
      /(?<![\d+])(?<v>(?:\+61[ ]?|\(?0)[2378]\)?[ -]?\d{4}[ -]?\d{4}|(?:\+61[ ]?|0)4\d{2}[ -]?\d{3}[ -]?\d{3}|1[38]00[ -]?\d{3}[ -]?\d{3}|13[ -]?\d{2}[ -]?\d{2})(?![\d-])/g,
    check: () => ({ region: true }),
    labelIds: ["phone"],
    base: 0.55,
    defaultEnabled: (value, signals) => signals.label === true || !BUSINESS_LINE.test(value.replace(/\D/g, "")),
    describe: (value) =>
      BUSINESS_LINE.test(value.replace(/\D/g, "")) ? "Business line (usually the insurer's)" : "Phone number",
  },
];

export function detectAU(ctx: PatternContext): Detection[] {
  return AU_PATTERNS.flatMap((spec) => runPattern(spec, ctx));
}
