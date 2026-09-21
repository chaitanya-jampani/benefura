"use client";

import { useId, type ReactNode } from "react";

import type { Benefit, Coverage, Limit, Plan } from "@/domain/types";
import { cn } from "@/lib/cn";

import {
  centsToDollarsInput,
  COVERAGE_LABELS,
  dollarsToCents,
  flattenBenefits,
  needsCheck,
  PERIOD_LABELS,
  periodFor,
  setBenefitPool,
  UNIT_LABELS,
  updateBenefit,
  type LimitUnit,
  type PeriodKind,
} from "./plan-edit";

const cell = "h-10 w-full min-w-0 rounded-xl bg-sunken px-3 text-base text-ink ring-1 ring-transparent focus:bg-surface focus:ring-ink/15 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus";
const selectCell = cn(cell, "cursor-pointer appearance-none pr-8");

function MobileLabel({ children }: { children: ReactNode }) {
  return <span className="mb-1 block text-sm text-muted md:hidden">{children}</span>;
}

function NumberCell({
  value,
  onChange,
  label,
  prefix,
  suffix,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  prefix?: string;
  suffix?: string;
}) {
  return (
    // Grows with the value so large maximums like $5000000 stay readable.
    <span className="relative flex flex-1 items-center" style={{ minWidth: `${Math.max(5.5, value.length * 0.6 + (prefix ? 2.25 : 1.5))}rem` }}>
      {prefix && <span className="pointer-events-none absolute left-3 text-muted">{prefix}</span>}
      <input
        inputMode="decimal"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(cell, prefix && "pl-7", suffix && "pr-8", "tabular-nums")}
      />
      {suffix && <span className="pointer-events-none absolute right-3 text-muted">{suffix}</span>}
    </span>
  );
}

function CoverageEditor({ benefit, onChange }: { benefit: Benefit; onChange: (c: Coverage) => void }) {
  const c = benefit.coverage;
  const name = benefit.name || "this benefit";
  const percentish = c.kind === "percent" || c.kind === "percent_capped" || c.kind === "schedule";
  const amountish = c.kind === "fixed_per_service" || c.kind === "per_diem";
  return (
    <div className="flex flex-col gap-2">
      <select
        aria-label={`Coverage type for ${name}`}
        value={c.kind}
        onChange={(e) => onChange({ ...c, kind: e.target.value as Coverage["kind"] })}
        className={selectCell}
      >
        {Object.entries(COVERAGE_LABELS).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
      <div className="flex gap-2">
        {percentish && (
          <NumberCell
            label={`Coverage percent for ${name}`}
            suffix="%"
            value={c.percent === null ? "" : String(c.percent)}
            onChange={(v) => {
              const n = v.trim() === "" ? null : Math.min(100, Math.max(0, Number(v)));
              onChange({ ...c, percent: n === null || Number.isNaN(n) ? null : n });
            }}
          />
        )}
        {c.kind === "percent_capped" && (
          <NumberCell
            label={`Cap per visit for ${name}`}
            prefix="$"
            value={centsToDollarsInput(c.capCents)}
            onChange={(v) => onChange({ ...c, capCents: dollarsToCents(v) })}
          />
        )}
        {amountish && (
          <NumberCell
            label={`Amount for ${name}`}
            prefix="$"
            value={centsToDollarsInput(c.amountCents)}
            onChange={(v) => onChange({ ...c, amountCents: dollarsToCents(v) })}
          />
        )}
      </div>
    </div>
  );
}

export function PlanTable({ plan, onChange }: { plan: Plan; onChange: (plan: Plan) => void }) {
  const ids = useId();
  const rows = flattenBenefits(plan).map((row, i, all) => ({ ...row, startsCategory: i === 0 || all[i - 1].categoryName !== row.categoryName }));

  const setLimit = (benefit: Benefit, limit: Limit | null) =>
    onChange(updateBenefit(plan, benefit.id, (b) => ({ ...b, limits: limit ? [limit, ...b.limits.slice(1)] : b.limits.slice(1) })));

  if (rows.length === 0) {
    return <p className="rounded-tile bg-sunken px-4 py-3 text-base text-ink-2">No benefits were found. Check the issues below, or fix pages and send them again.</p>;
  }

  return (
    <table className="w-full border-separate border-spacing-0 text-left" aria-describedby={`${ids}-caption`}>
      <caption id={`${ids}-caption`} className="sr-only">
        Extracted benefits. Every field can be edited before saving.
      </caption>
      <thead className="max-md:sr-only">
        <tr className="text-sm text-muted">
          <th scope="col" className="w-[21%] pb-3 font-normal">Benefit</th>
          <th scope="col" className="w-[22%] pb-3 pl-3 font-normal">Coverage</th>
          <th scope="col" className="w-[25%] pb-3 pl-3 font-normal">Limit</th>
          <th scope="col" className="w-[16%] pb-3 pl-3 font-normal">Period</th>
          <th scope="col" className="w-[16%] pb-3 pl-3 font-normal">Pool</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ benefit, categoryName, startsCategory }) => {
          const header =
            startsCategory ? (
              <tr key={`cat-${benefit.categoryId}`}>
                <th scope="colgroup" colSpan={5} className="pt-6 pb-2 text-lg font-medium text-ink first:pt-0">
                  {categoryName}
                </th>
              </tr>
            ) : null;
          const limit = benefit.limits[0];
          const check = needsCheck(benefit);
          const name = benefit.name || "this benefit";
          return [
            header,
            <tr key={benefit.id} className="align-top max-md:grid max-md:grid-cols-2 max-md:gap-3 max-md:border-t max-md:border-line max-md:py-4">
              <td className="py-2 max-md:col-span-2 md:border-t md:border-line">
                <MobileLabel>Benefit</MobileLabel>
                <div className="flex items-center gap-2">
                  {check && <span aria-hidden className="size-2 shrink-0 rounded-full bg-caution" />}
                  <input
                    aria-label={`Benefit name${check ? " (check this row)" : ""}`}
                    value={benefit.name}
                    onChange={(e) => onChange(updateBenefit(plan, benefit.id, (b) => ({ ...b, name: e.target.value })))}
                    className={cn(cell, "font-medium")}
                  />
                </div>
              </td>
              <td className="py-2 max-md:col-span-2 md:border-t md:border-line md:pl-3">
                <MobileLabel>Coverage</MobileLabel>
                <CoverageEditor
                  benefit={benefit}
                  onChange={(coverage) => onChange(updateBenefit(plan, benefit.id, (b) => ({ ...b, coverage })))}
                />
              </td>
              <td className="py-2 md:border-t md:border-line md:pl-3">
                <MobileLabel>Limit</MobileLabel>
                <div className="flex flex-wrap gap-2">
                  {limit && (
                    <NumberCell
                      label={`Limit for ${name}`}
                      prefix={limit.unit === "cents" ? "$" : undefined}
                      value={limit.unit === "cents" ? centsToDollarsInput(limit.value) : String(limit.value)}
                      onChange={(v) => {
                        const value = limit.unit === "cents" ? dollarsToCents(v) : Math.max(0, Math.round(Number(v) || 0));
                        setLimit(benefit, { ...limit, value: value ?? 0 });
                      }}
                    />
                  )}
                  <select
                    aria-label={`Limit unit for ${name}`}
                    value={limit ? limit.unit : "none"}
                    onChange={(e) => {
                      const unit = e.target.value;
                      if (unit === "none") return setLimit(benefit, null);
                      setLimit(benefit, {
                        unit: unit as LimitUnit,
                        value: limit && limit.unit === unit ? limit.value : 0,
                        period: limit?.period ?? { ...plan.benefitPeriod },
                        scope: limit?.scope ?? "per_person",
                      });
                    }}
                    className={cn(selectCell, limit ? "w-auto min-w-[5.5rem] flex-1" : "")}
                  >
                    <option value="none">No limit</option>
                    {Object.entries(UNIT_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                  {benefit.limits.length > 1 && <span className="text-sm text-muted">+{benefit.limits.length - 1} more</span>}
                </div>
              </td>
              <td className="py-2 md:border-t md:border-line md:pl-3">
                <MobileLabel>Period</MobileLabel>
                <div className="flex flex-col gap-2">
                  <select
                    aria-label={`Limit period for ${name}`}
                    value={limit?.period.kind ?? ""}
                    disabled={!limit}
                    onChange={(e) => limit && setLimit(benefit, { ...limit, period: periodFor(e.target.value as PeriodKind, limit.period) })}
                    className={cn(selectCell, "disabled:opacity-40")}
                  >
                    {!limit && <option value="">None</option>}
                    {Object.entries(PERIOD_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                  {limit?.period.kind === "rolling_months" && (
                    <NumberCell
                      label={`Months for ${name}`}
                      suffix="mo"
                      value={String(limit.period.months ?? "")}
                      onChange={(v) => setLimit(benefit, { ...limit, period: { ...limit.period, months: Math.max(1, Math.round(Number(v) || 1)) } })}
                    />
                  )}
                  {limit?.period.kind === "consecutive_benefit_years" && (
                    <NumberCell
                      label={`Years for ${name}`}
                      suffix="yr"
                      value={String(limit.period.years ?? "")}
                      onChange={(v) => setLimit(benefit, { ...limit, period: { ...limit.period, years: Math.max(1, Math.round(Number(v) || 1)) } })}
                    />
                  )}
                </div>
              </td>
              <td className="py-2 max-md:col-span-2 md:border-t md:border-line md:pl-3">
                <MobileLabel>Pool</MobileLabel>
                <select
                  aria-label={`Shared maximum for ${name}`}
                  value={benefit.poolId ?? ""}
                  onChange={(e) => onChange(setBenefitPool(plan, benefit.id, e.target.value || null))}
                  className={selectCell}
                >
                  <option value="">None</option>
                  {plan.limitPools.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </td>
            </tr>,
            check && benefit.source ? (
              <tr key={`${benefit.id}-source`} className="max-md:block">
                <td colSpan={5} className="pb-3 max-md:block">
                  <p className="rounded-xl bg-caution/8 px-3 py-2 text-sm text-ink-2">
                    <span className="font-medium text-ink">Check this row. </span>
                    {benefit.source.verifierVerdict === "unsupported" ? "The checker couldn't confirm it. " : "Low confidence. "}
                    <q className="italic">{benefit.source.quote}</q> · page {benefit.source.page}
                  </p>
                </td>
              </tr>
            ) : null,
          ];
        })}
      </tbody>
    </table>
  );
}
