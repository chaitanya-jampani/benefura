import type { Detection } from "../types";
import { bcPhnValid, ontarioHealthValid, ramqValid, sinValid } from "./checksums";
import { runPattern, type PatternContext, type PatternSpec } from "./common";

const HEALTH_LABELS = ["health", "health_acronym", "certificate"] as const;
const PROVINCE = /(?:AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT|Alta\.?|Ont\.?|Que\.?|Man\.?|Sask\.?)[\s,]*$/;
const TOLL_FREE = /^(?:800|833|844|855|866|877|888)$/;

export const CA_PATTERNS: readonly PatternSpec[] = [
  {
    detector: "ca.sin",
    label: "Social insurance number",
    kind: "identifier",
    family: "ID",
    regex: /(?<![\d-])(?<v>\d{3}[ -]?\d{3}[ -]?\d{3})(?![\d-])/g,
    check: (value) => ({ checksum: sinValid(value), region: true }),
    labelIds: ["sin", "sin_acronym"],
    requireChecksumOrLabel: true,
    base: 0.3,
  },
  {
    detector: "ca.on_health",
    label: "Ontario health card number",
    kind: "identifier",
    family: "ID",
    regex: /(?<![\d-])(?<v>\d{4}[ -]?\d{3}[ -]?\d{3}(?:[ -]?[A-Z]{2}(?![\p{L}\p{N}]))?)(?![\d-])/gu,
    check: (value) => {
      if (value.replace(/\D/g, "")[0] === "9" && bcPhnValid(value)) return null;
      return ontarioHealthValid(value) ? { checksum: true, region: true } : { region: true };
    },
    labelIds: HEALTH_LABELS,
    requireChecksumOrLabel: true,
    base: 0.3,
  },
  {
    detector: "ca.bc_phn",
    label: "BC personal health number",
    kind: "identifier",
    family: "ID",
    regex: /(?<![\d-])(?<v>9\d{3}[ -]?\d{3}[ -]?\d{3})(?![\d-])/g,
    check: (value) => (bcPhnValid(value) ? { checksum: true, region: true } : { region: true }),
    labelIds: HEALTH_LABELS,
    requireChecksumOrLabel: true,
    base: 0.3,
  },
  {
    detector: "ca.ramq",
    label: "Québec health insurance number",
    kind: "identifier",
    family: "ID",
    regex: /(?<![\p{L}\p{N}])(?<v>[A-Z]{4}[ -]?\d{4}[ -]?\d{4})(?![\p{L}\p{N}])/gu,
    // No public check digit: a valid embedded birth date is the strongest signal available.
    check: (value) => (ramqValid(value) ? { region: true } : null),
    labelIds: HEALTH_LABELS,
    base: 0.55,
  },
  {
    detector: "ca.postal",
    label: "Postal code",
    kind: "address",
    family: null,
    regex: /(?<![\p{L}\p{N}])(?<v>[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d)(?![\p{L}\p{N}])/gu,
    check: (_value, match, ctx) => ({ region: PROVINCE.test(ctx.stream.text.slice(Math.max(0, match.index - 12), match.index)) }),
    labelIds: ["address"],
    base: 0.6,
  },
  {
    detector: "ca.phone",
    label: "Phone number",
    kind: "phone",
    family: "PHONE",
    regex: /(?<![\d+])(?<v>(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4})(?![\d-])/g,
    check: (value) => {
      const d = value.replace(/\D/g, "").replace(/^1(?=\d{10}$)/, "");
      if (d.length !== 10 || d[0] < "2" || d[3] < "2") return null;
      return { region: true };
    },
    labelIds: ["phone"],
    base: 0.55,
    // Toll-free lines on a booklet are the insurer's; leave them visible unless the user opts in.
    defaultEnabled: (value, signals) => signals.label === true || !TOLL_FREE.test(areaCode(value)),
    describe: (value) => (TOLL_FREE.test(areaCode(value)) ? "Toll-free number (usually the insurer's)" : "Phone number"),
  },
];

function areaCode(value: string): string {
  return value.replace(/\D/g, "").replace(/^1(?=\d{10}$)/, "").slice(0, 3);
}

export function detectCA(ctx: PatternContext): Detection[] {
  return CA_PATTERNS.flatMap((spec) => runPattern(spec, ctx));
}
