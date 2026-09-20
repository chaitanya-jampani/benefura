import { describe, expect, it } from "vitest";

import {
  abnValid,
  auPostcodeMatchesState,
  bcPhnValid,
  ihiValid,
  luhnValid,
  medicareValid,
  ontarioHealthValid,
  ramqValid,
  sinValid,
  tfnValid,
} from "./checksums";

/** Every single-digit change of a valid number, which a check digit must reject. */
function mutations(value: string): string[] {
  const out: string[] = [];
  for (let i = 0; i < value.length; i++) {
    const ch = value[i];
    if (!/\d/.test(ch)) continue;
    for (let d = 0; d <= 9; d++) {
      if (String(d) === ch) continue;
      out.push(value.slice(0, i) + d + value.slice(i + 1));
    }
  }
  return out;
}

const CASES: Array<{ name: string; fn: (v: string) => boolean; valid: string[]; skipMutationAt?: number[] }> = [
  { name: "Luhn", fn: luhnValid, valid: ["79927398713", "4532015112830366"] },
  { name: "SIN", fn: sinValid, valid: ["046 454 286", "130-692-544", "130692544"] },
  { name: "Ontario health number", fn: ontarioHealthValid, valid: ["9876 543 217", "1234-567-897"] },
  { name: "BC PHN", fn: bcPhnValid, valid: ["9698 658 215"] },
  { name: "TFN (9 digits)", fn: tfnValid, valid: ["123 456 782", "876 543 210"] },
  { name: "TFN (8 digits)", fn: tfnValid, valid: ["12345677"] },
  // The 10th digit is the card issue number and is not covered by the check digit.
  { name: "Medicare", fn: medicareValid, valid: ["2951 23457 1", "2123 45670 1"], skipMutationAt: [9] },
  { name: "IHI", fn: ihiValid, valid: ["8003 6012 3456 7894"] },
  { name: "ABN", fn: abnValid, valid: ["51 824 753 556"] },
];

describe.each(CASES)("$name", ({ fn, valid, skipMutationAt = [] }) => {
  it.each(valid)("accepts %s", (value) => {
    expect(fn(value)).toBe(true);
  });

  it.each(valid)("rejects every single-digit mutation of %s", (value) => {
    const digitsOnly = value.replace(/\D/g, "");
    const skip = new Set(skipMutationAt);
    const accepted = mutations(digitsOnly).filter((m, i) => {
      const position = Math.floor(i / 9);
      return !skip.has(position) && fn(m);
    });
    expect(accepted).toEqual([]);
  });
});

describe("structural rules", () => {
  it("rejects wrong lengths and prefixes", () => {
    expect(sinValid("046 454 28")).toBe(false);
    expect(sinValid("846 454 286")).toBe(false);
    expect(bcPhnValid("1698658215")).toBe(false);
    expect(ihiValid("8003611234567893")).toBe(false);
    expect(medicareValid("7951234571")).toBe(false);
    expect(medicareValid("29512345710")).toBe(false);
    expect(tfnValid("1234567")).toBe(false);
  });

  it("validates RAMQ shape and embedded birth date", () => {
    expect(ramqValid("TESJ 8506 1512")).toBe(true);
    expect(ramqValid("TESJ 8556 1512")).toBe(true); // month + 50
    expect(ramqValid("TESJ 8513 1512")).toBe(false);
    expect(ramqValid("TESJ 8506 3212")).toBe(false);
    expect(ramqValid("TES1 8506 1512")).toBe(false);
  });

  it("matches AU postcodes to their state", () => {
    expect(auPostcodeMatchesState("NSW", "2000")).toBe(true);
    expect(auPostcodeMatchesState("VIC", "3000")).toBe(true);
    expect(auPostcodeMatchesState("ACT", "2600")).toBe(true);
    expect(auPostcodeMatchesState("NT", "0800")).toBe(true);
    expect(auPostcodeMatchesState("QLD", "2000")).toBe(false);
  });
});
