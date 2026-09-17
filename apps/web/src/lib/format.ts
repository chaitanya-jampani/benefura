import type { Currency } from "@/domain/types";

const THIN_SPACE = " ";

const LOCALE: Record<Currency, string> = { CAD: "en-CA", AUD: "en-AU" };

function groupThin(n: number, fractionDigits: number): string {
  const fixed = Math.abs(n).toFixed(fractionDigits);
  const [whole, frac] = fixed.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_SPACE);
  return frac ? `${grouped}.${frac}` : grouped;
}

export interface MoneyParts {
  sign: "" | "-";
  symbol: string;
  whole: string;
  cents: string;
}

export function moneyParts(cents: number, currency: Currency = "CAD"): MoneyParts {
  void currency; // CAD and AUD both use "$".
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(Math.round(cents));
  return {
    sign,
    symbol: "$",
    whole: groupThin(Math.floor(abs / 100), 0),
    cents: String(abs % 100).padStart(2, "0"),
  };
}

export function formatMoney(cents: number, currency: Currency = "CAD", opts: { whole?: boolean } = {}): string {
  const p = moneyParts(cents, currency);
  return `${p.sign}${p.symbol}${p.whole}${opts.whole ? "" : `.${p.cents}`}`;
}

export function formatPercent(value: number, fractionDigits = 0): string {
  return `${value.toFixed(fractionDigits)}%`;
}

// Formatted in UTC so an ISO local date never shifts by a day.
export function formatDate(iso: string, currency: Currency = "CAD"): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return new Intl.DateTimeFormat(LOCALE[currency], {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
