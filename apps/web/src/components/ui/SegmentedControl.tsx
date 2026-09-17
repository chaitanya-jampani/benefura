"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface Segment<T extends string> {
  value: T;
  label: ReactNode;
  ariaLabel?: string;
}

export function SegmentedControl<T extends string>({
  value,
  onChange,
  segments,
  label,
  className,
  size = "md",
}: {
  value: T;
  onChange: (value: T) => void;
  segments: Segment<T>[];
  label: string;
  className?: string;
  size?: "sm" | "md";
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const onKey = (event: KeyboardEvent, index: number) => {
    const delta = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const next = (index + delta + segments.length) % segments.length;
    onChange(segments[next].value);
    refs.current[next]?.focus();
  };
  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex flex-wrap gap-1 rounded-full bg-sunken p-1", className)}>
      {segments.map((segment, i) => {
        const selected = segment.value === value;
        return (
          <button
            key={segment.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={segment.ariaLabel}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(segment.value)}
            onKeyDown={(e) => onKey(e, i)}
            className={cn(
              "rounded-full whitespace-nowrap transition-[background-color,color,box-shadow] duration-150",
              size === "sm" ? "h-8 px-2.5 text-[0.8125rem] sm:px-3 sm:text-sm" : "h-10 px-4 text-base",
              selected ? "bg-surface text-ink shadow-tile" : "text-muted hover:text-ink-2",
            )}
          >
            {segment.label}
          </button>
        );
      })}
    </div>
  );
}
