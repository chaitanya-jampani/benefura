"use client";

import { useId, type ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
  className,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cn("flex items-center justify-between gap-4", className)}>
      <span className="flex min-w-0 flex-col">
        <span id={`${id}-label`} className="text-base text-ink">
          {label}
        </span>
        {description && (
          <span id={`${id}-desc`} className="text-sm text-muted">
            {description}
          </span>
        )}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-label`}
        aria-describedby={description ? `${id}-desc` : undefined}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-8 w-14 shrink-0 rounded-full transition-colors duration-200 disabled:opacity-40",
          checked ? "bg-ink" : "bg-line shadow-[inset_0_1px_2px_rgb(24_24_48/0.08)]",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "absolute top-1 left-1 size-6 rounded-full bg-surface shadow-tile transition-transform duration-200",
            checked && "translate-x-6",
          )}
        />
      </button>
    </div>
  );
}
