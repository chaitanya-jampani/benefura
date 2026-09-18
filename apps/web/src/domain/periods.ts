// Pure date math on ISO local dates. Only `todayIso` touches Date or time zones, so nothing drifts across DST.
import type { Benefit, IsoDate, Period, Plan } from "./types";

/** Inclusive date range. */
export interface DateRange {
  start: IsoDate;
  end: IsoDate;
}

export const LIFETIME: DateRange = { start: "0001-01-01", end: "9999-12-31" };

type PlanDates = Pick<Plan, "benefitPeriod" | "effectiveDate">;

interface Ymd {
  y: number;
  m: number;
  d: number;
}

export function parseIso(date: IsoDate): Ymd {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!match) throw new RangeError(`Not an ISO date: ${date}`);
  return { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) };
}

export function formatIso({ y, m, d }: Ymd): IsoDate {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

export function isIsoDate(value: unknown): value is IsoDate {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const { y, m, d } = parseIso(value);
  return y >= 1 && m >= 1 && m <= 12 && d >= 1 && d <= daysInMonth(y, m);
}

function isLeap(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

export function daysInMonth(y: number, m: number): number {
  return [31, isLeap(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];
}

// Howard Hinnant's days_from_civil: days since 1970-01-01.
function toDayNumber({ y, m, d }: Ymd): number {
  const yy = m <= 2 ? y - 1 : y;
  const era = Math.floor(yy / 400);
  const yoe = yy - era * 400;
  const doy = Math.floor((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}

function fromDayNumber(n: number): Ymd {
  const z = n + 719468;
  const era = Math.floor(z / 146097);
  const doe = z - era * 146097;
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const d = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const m = mp < 10 ? mp + 3 : mp - 9;
  return { y: yoe + era * 400 + (m <= 2 ? 1 : 0), m, d };
}

export function addDays(date: IsoDate, days: number): IsoDate {
  return formatIso(fromDayNumber(toDayNumber(parseIso(date)) + Math.trunc(days)));
}

/** Clamps the day to the target month (Jan 31 + 1 month = Feb 28/29). */
export function addMonths(date: IsoDate, months: number): IsoDate {
  const { y, m, d } = parseIso(date);
  const index = y * 12 + (m - 1) + Math.trunc(months);
  const ty = Math.floor(index / 12);
  const tm = index - ty * 12 + 1;
  return formatIso({ y: ty, m: tm, d: Math.min(d, daysInMonth(ty, tm)) });
}

export function addYears(date: IsoDate, years: number): IsoDate {
  return addMonths(date, years * 12);
}

/** Positive when `b` is later. */
export function daysBetween(a: IsoDate, b: IsoDate): number {
  return toDayNumber(parseIso(b)) - toDayNumber(parseIso(a));
}

export function compareDates(a: IsoDate, b: IsoDate): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function inRange(date: IsoDate, range: DateRange): boolean {
  return date >= range.start && date <= range.end;
}

export function maxDate(a: IsoDate, b: IsoDate): IsoDate {
  return a >= b ? a : b;
}

export function minDate(a: IsoDate, b: IsoDate): IsoDate {
  return a <= b ? a : b;
}

export function todayIso(tz?: string): IsoDate {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

interface YearAnchor {
  month: number;
  day: number;
}

function anchorOn(y: number, a: YearAnchor): IsoDate {
  return formatIso({ y, m: a.month, d: Math.min(a.day, daysInMonth(y, a.month)) });
}

function anniversaryAnchor(plan: PlanDates): YearAnchor {
  if (plan.effectiveDate) {
    const { m, d } = parseIso(plan.effectiveDate);
    return { month: m, day: d };
  }
  return { month: 1, day: 1 };
}

// A benefit-year period without its own start follows the plan's benefit year, then January 1.
function benefitYearAnchor(period: Period, plan: PlanDates): YearAnchor {
  if (period.startMonth != null) return { month: period.startMonth, day: period.startDay ?? 1 };
  const own = plan.benefitPeriod;
  if (own.kind === "benefit_year" && own.startMonth != null) return { month: own.startMonth, day: own.startDay ?? 1 };
  if (own.kind === "policy_anniversary") return anniversaryAnchor(plan);
  return { month: 1, day: 1 };
}

function yearWindow(anchor: YearAnchor, date: IsoDate): DateRange {
  const { y } = parseIso(date);
  const thisYear = anchorOn(y, anchor);
  const startYear = thisYear <= date ? y : y - 1;
  return { start: anchorOn(startYear, anchor), end: addDays(anchorOn(startYear + 1, anchor), -1) };
}

/**
 * Rolling windows end on `serviceDate`; consecutive benefit years align to the plan's first benefit year.
 * Per-visit periods return null because each service stands alone.
 */
export function periodWindow(period: Period, serviceDate: IsoDate, plan: PlanDates): DateRange | null {
  switch (period.kind) {
    case "benefit_year":
      return yearWindow(benefitYearAnchor(period, plan), serviceDate);
    case "policy_anniversary":
      return yearWindow(anniversaryAnchor(plan), serviceDate);
    case "rolling_months": {
      const months = period.months ?? 12;
      return { start: addDays(addMonths(serviceDate, -months), 1), end: serviceDate };
    }
    case "consecutive_benefit_years": {
      const years = Math.max(1, period.years ?? 1);
      const anchor = benefitYearAnchor(period, plan);
      const first = yearWindow(anchor, plan.effectiveDate ?? anchorOn(1, anchor)).start;
      const current = yearWindow(anchor, serviceDate).start;
      const elapsed = parseIso(current).y - parseIso(first).y;
      const startYear = parseIso(first).y + Math.floor(elapsed / years) * years;
      return { start: anchorOn(startYear, anchor), end: addDays(anchorOn(startYear + years, anchor), -1) };
    }
    case "lifetime":
      return LIFETIME;
    case "per_visit":
    case "per_admission":
      return null;
  }
}

export function currentBenefitPeriod(plan: PlanDates, today: IsoDate): DateRange {
  const window = periodWindow(plan.benefitPeriod, today, plan);
  if (!window || window === LIFETIME) return yearWindow({ month: 1, day: 1 }, today);
  return window;
}

/** Null when the window never resets; rolling windows slide instead. */
export function nextResetDate(period: Period, plan: PlanDates, today: IsoDate): IsoDate | null {
  if (period.kind === "rolling_months") return null;
  const window = periodWindow(period, today, plan);
  if (!window || window === LIFETIME) return null;
  return addDays(window.end, 1);
}

/** When both `submissionDays` and `daysAfterPeriodEnd` are set, the later deadline wins. */
export function claimDeadline(plan: Pick<Plan, "benefitPeriod" | "effectiveDate" | "claimRules">, serviceDate: IsoDate): IsoDate | null {
  const rules = plan.claimRules;
  const candidates: IsoDate[] = [];
  if (rules.submissionDays != null) candidates.push(addDays(serviceDate, rules.submissionDays));
  if (rules.daysAfterPeriodEnd != null) {
    const window = periodWindow(plan.benefitPeriod, serviceDate, plan);
    if (window && window !== LIFETIME) candidates.push(addDays(window.end, rules.daysAfterPeriodEnd));
  }
  if (candidates.length === 0) return null;
  return candidates.reduce(maxDate);
}

/** First covered day after the waiting period. */
export function waitingPeriodEnds(benefit: Benefit, plan: PlanDates, coverageStart?: IsoDate): IsoDate | null {
  const months = benefit.waitingPeriod?.months ?? 0;
  const start = coverageStart ?? plan.effectiveDate;
  if (months <= 0 || !start) return null;
  return addMonths(start, months);
}
