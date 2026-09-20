"use client";

import { Check, LoaderCircle } from "lucide-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface StepDef {
  id: number;
  label: string;
}

export function Stepper({ steps, current, className }: { steps: readonly StepDef[]; current: number; className?: string }) {
  const active = steps.find((s) => s.id === current);
  return (
    <nav aria-label="Redaction steps" className={className}>
      <p className="mb-3 text-base text-muted sm:hidden">
        Step {current} of {steps.length}
        <span className="text-ink"> · {active?.label}</span>
      </p>
      <div className="flex gap-1.5 sm:hidden" aria-hidden>
        {steps.map((s) => (
          <span key={s.id} className={cn("h-1 flex-1 rounded-full", s.id <= current ? "bg-ink" : "bg-faint/60")} />
        ))}
      </div>
      <ol className="hidden items-center gap-3 sm:flex">
        {steps.map((s, i) => {
          const done = s.id < current;
          const isCurrent = s.id === current;
          return (
            <li key={s.id} className="flex min-w-0 flex-1 items-center gap-3" aria-current={isCurrent ? "step" : undefined}>
              <span
                className={cn(
                  "grid size-8 shrink-0 place-items-center rounded-full text-sm font-medium transition-colors",
                  isCurrent && "bg-ink text-white",
                  done && "bg-surface text-ink shadow-tile",
                  !isCurrent && !done && "bg-transparent text-muted ring-1 ring-faint",
                )}
              >
                {done ? <Check aria-hidden className="size-4" strokeWidth={1.5} /> : s.id}
                {done && <span className="sr-only">Done: </span>}
              </span>
              <span className={cn("truncate text-base", isCurrent ? "text-ink" : "text-muted")}>{s.label}</span>
              {i < steps.length - 1 && <span aria-hidden className="h-px min-w-4 flex-1 bg-faint/60" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** Tokens use the same plain sans-serif as the burned-in labels, so both read identically. */
export const TOKEN_FONT = { fontFamily: 'Helvetica, Arial, "Liberation Sans", sans-serif' } as const;

export function TokenPill({ token, className }: { token: string | null; className?: string }) {
  if (!token) {
    return (
      <span className={cn("inline-flex h-7 items-center gap-1.5 rounded-full bg-ink px-2.5 text-xs font-medium text-white", className)}>
        <span aria-hidden className="size-2 rounded-[2px] bg-white" />
        Black box
      </span>
    );
  }
  return (
    <span
      style={TOKEN_FONT}
      className={cn(
        "inline-flex h-7 items-center rounded-full bg-surface px-2.5 text-xs font-semibold text-ink ring-1 ring-ink/80",
        className,
      )}
    >
      {token}
    </span>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  className,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors duration-150 disabled:opacity-40",
        checked ? "bg-ink" : "bg-line ring-1 ring-faint/60",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute top-1 size-5 rounded-full bg-white shadow-tile transition-transform duration-150",
          checked ? "translate-x-6" : "translate-x-1",
        )}
      />
    </button>
  );
}

export function Progress({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? Math.round((Math.min(value, max) / max) * 100) : 0;
  return (
    <div className="flex flex-col gap-2" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-base text-ink-2">
        <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" strokeWidth={1.5} />
        <span>{label}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-line" aria-hidden>
        <div className="h-full rounded-full bg-ink transition-[width] duration-300" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Field({
  label,
  hint,
  htmlFor,
  trailing,
  children,
}: {
  label: string;
  hint?: ReactNode;
  htmlFor: string;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={htmlFor} className="text-base font-medium text-ink">
          {label}
        </label>
        {trailing}
      </div>
      {children}
      {hint && <p className="text-sm text-muted">{hint}</p>}
    </div>
  );
}

export const inputClass =
  "h-12 w-full rounded-tile bg-sunken px-4 text-base text-ink placeholder:text-faint ring-1 ring-transparent transition-shadow focus:bg-surface focus:ring-ink/15 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

export function TextInput(props: ComponentPropsWithoutRef<"input">) {
  return <input {...props} className={cn(inputClass, props.className)} />;
}

export function Notice({
  tone = "muted",
  icon,
  title,
  children,
  className,
}: {
  tone?: "muted" | "caution" | "negative";
  icon?: ReactNode;
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role={tone === "muted" ? undefined : "alert"}
      className={cn(
        "flex gap-3 rounded-tile px-4 py-3 text-base",
        tone === "muted" && "bg-sunken text-ink-2",
        tone === "caution" && "bg-caution/8 text-ink-2 ring-1 ring-caution/25",
        tone === "negative" && "bg-negative/6 text-ink-2 ring-1 ring-negative/25",
        className,
      )}
    >
      {icon && <span className={cn("mt-0.5 shrink-0", tone === "caution" && "text-caution", tone === "negative" && "text-negative")}>{icon}</span>}
      <div className="flex min-w-0 flex-col gap-1">
        {title && <p className="font-medium text-ink">{title}</p>}
        {children}
      </div>
    </div>
  );
}
