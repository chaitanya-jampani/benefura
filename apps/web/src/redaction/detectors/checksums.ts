export function digitsOf(value: string): string {
  return value.replace(/\D/g, "");
}

export function luhnValid(value: string): boolean {
  const d = digitsOf(value);
  if (d.length < 2) return false;
  let sum = 0;
  for (let i = 0; i < d.length; i++) {
    let n = d.charCodeAt(d.length - 1 - i) - 48;
    if (i % 2 === 1) {
      n *= 2;
      if (n > 9) n -= 9;
    }
    sum += n;
  }
  return sum % 10 === 0;
}

export function weightedSum(digits: string, weights: readonly number[]): number {
  let sum = 0;
  for (let i = 0; i < weights.length; i++) sum += (digits.charCodeAt(i) - 48) * weights[i];
  return sum;
}

/** SIN: 9 digits, Luhn; a leading 8 is never issued. */
export function sinValid(value: string): boolean {
  const d = digitsOf(value);
  return d.length === 9 && d[0] !== "8" && luhnValid(d);
}

/** Ontario health number: 10 digits, Luhn over all 10; version code letters aren't checked. */
export function ontarioHealthValid(value: string): boolean {
  const d = digitsOf(value);
  return d.length === 10 && d[0] !== "0" && luhnValid(d);
}

const BC_PHN_WEIGHTS = [2, 4, 8, 5, 10, 9, 7, 3] as const;

/** BC PHN: 10 digits starting 9; digits 2–9 × 2,4,8,5,10,9,7,3 (each mod 11), check = 11 − sum mod 11. */
export function bcPhnValid(value: string): boolean {
  const d = digitsOf(value);
  if (d.length !== 10 || d[0] !== "9") return false;
  let sum = 0;
  for (let i = 0; i < BC_PHN_WEIGHTS.length; i++) sum += ((d.charCodeAt(i + 1) - 48) * BC_PHN_WEIGHTS[i]) % 11;
  const check = 11 - (sum % 11);
  return check <= 9 && check === d.charCodeAt(9) - 48;
}

/** RAMQ: 4 letters, YYMMDD, 2 digits; no public check digit, so only the birth date is checked (month + 50 for women). */
export function ramqValid(value: string): boolean {
  const m = /^([A-Z]{4})[\s-]?(\d{2})(\d{2})[\s-]?(\d{2})(\d{2})$/.exec(value.trim().toUpperCase());
  if (!m) return false;
  let month = Number(m[3]);
  if (month > 50) month -= 50;
  const day = Number(m[4]);
  return month >= 1 && month <= 12 && day >= 1 && day <= 31;
}

const TFN9_WEIGHTS = [1, 4, 3, 7, 5, 8, 6, 9, 10] as const;
const TFN8_WEIGHTS = [10, 7, 8, 4, 6, 3, 5, 1] as const;

/** TFN: 8 or 9 digits, weighted sum mod 11 = 0. */
export function tfnValid(value: string): boolean {
  const d = digitsOf(value);
  if (d.length === 9) return weightedSum(d, TFN9_WEIGHTS) % 11 === 0;
  if (d.length === 8) return weightedSum(d, TFN8_WEIGHTS) % 11 === 0;
  return false;
}

const MEDICARE_WEIGHTS = [1, 3, 7, 9, 1, 3, 7, 9] as const;

/** Medicare: 10 digits (+ optional IRN) starting 2–6; digits 1–8 × 1,3,7,9,1,3,7,9 mod 10 = 9th digit. */
export function medicareValid(value: string): boolean {
  const d = digitsOf(value);
  if (d.length !== 10 && d.length !== 11) return false;
  if (d[0] < "2" || d[0] > "6") return false;
  if (d.length === 11 && d[10] === "0") return false;
  return weightedSum(d, MEDICARE_WEIGHTS) % 10 === d.charCodeAt(8) - 48;
}

/** IHI: 16 digits, prefix 800360, Luhn. */
export function ihiValid(value: string): boolean {
  const d = digitsOf(value);
  return d.length === 16 && d.startsWith("800360") && luhnValid(d);
}

const ABN_WEIGHTS = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19] as const;

/** ABN: 11 digits; first digit − 1, weighted sum mod 89 = 0. */
export function abnValid(value: string): boolean {
  const d = digitsOf(value);
  if (d.length !== 11 || d[0] === "0") return false;
  const adjusted = String(Number(d[0]) - 1) + d.slice(1);
  return weightedSum(adjusted, ABN_WEIGHTS) % 89 === 0;
}

/** Australia Post postcode ranges per state. */
export function auPostcodeMatchesState(state: string, postcode: string): boolean {
  const n = Number(postcode);
  if (!/^\d{4}$/.test(postcode)) return false;
  const inRange = (ranges: Array<[number, number]>) => ranges.some(([a, b]) => n >= a && n <= b);
  switch (state.toUpperCase()) {
    case "NSW":
      return inRange([[1000, 2599], [2619, 2899], [2921, 2999]]);
    case "ACT":
      return inRange([[200, 299], [2600, 2618], [2900, 2920]]);
    case "VIC":
      return inRange([[3000, 3999], [8000, 8999]]);
    case "QLD":
      return inRange([[4000, 4999], [9000, 9999]]);
    case "SA":
      return inRange([[5000, 5999]]);
    case "WA":
      return inRange([[6000, 6999]]);
    case "TAS":
      return inRange([[7000, 7999]]);
    case "NT":
      return inRange([[800, 999]]);
    default:
      return false;
  }
}
