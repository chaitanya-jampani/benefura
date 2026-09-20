"use client";

import { useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { BenefitGlyph } from "@/components/dashboard/BenefitIcon";
import { indexPlan } from "@/domain/ledger";
import { findBenefits } from "@/domain/search";
import type { Plan } from "@/domain/types";
import { cn } from "@/lib/cn";

interface Option {
  benefitId: string;
  name: string;
  categoryName: string;
}

export function BenefitPicker({
  plan,
  value,
  onChange,
  id,
  invalid,
}: {
  plan: Plan;
  value: string;
  onChange: (benefitId: string) => void;
  id: string;
  invalid?: boolean;
}) {
  const index = indexPlan(plan);
  const selected = value ? index.benefits.get(value) : undefined;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  const options: Option[] = useMemo(() => {
    if (query.trim()) return findBenefits(plan, query, 8);
    return plan.categories.flatMap((c) => c.benefits.map((b) => ({ benefitId: b.id, name: b.name, categoryName: c.name })));
  }, [plan, query]);

  const choose = (option: Option) => {
    onChange(option.benefitId);
    setQuery("");
    setOpen(false);
  };

  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(options.length - 1, a + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (event.key === "Enter" && open && options[active]) {
      event.preventDefault();
      choose(options[active]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  if (selected && !open) {
    const category = index.categories.get(selected.categoryId);
    return (
      <button
        id={id}
        type="button"
        onClick={() => {
          setOpen(true);
          setActive(0);
          requestAnimationFrame(() => inputRef.current?.focus());
        }}
        className="flex h-14 w-full min-w-0 items-center gap-3 rounded-full bg-surface pr-5 pl-2 text-left shadow-tile hover:bg-sunken"
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-sunken">
          <BenefitGlyph benefit={selected} kind={category?.kind} className="size-5 sm:size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-base text-ink">{selected.name}</span>
          {category && category.name !== selected.name && <span className="block truncate text-sm text-muted">{category.name}</span>}
        </span>
        <span className="shrink-0 text-sm text-ink-2">Change</span>
      </button>
    );
  }

  return (
    <div className="relative">
      <input
        ref={inputRef}
        id={id}
        type="search"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-invalid={invalid || undefined}
        aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        placeholder="Search, e.g. massage, glasses or an item number"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={onKey}
        className={cn(
          "h-12 w-full min-w-0 rounded-full bg-sunken px-5 text-base text-ink ring-1 placeholder:text-faint focus-visible:bg-surface",
          invalid ? "ring-negative/40" : "ring-transparent focus-visible:ring-line",
        )}
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Benefits"
          className="absolute inset-x-0 top-14 z-30 max-h-80 overflow-auto rounded-tile bg-surface p-2 shadow-float"
        >
          {options.length === 0 && <li className="px-3 py-2 text-base text-muted">No benefit matches “{query}”.</li>}
          {options.map((option, i) => (
            <li
              key={option.benefitId}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(option)}
              onMouseEnter={() => setActive(i)}
              className={cn("flex cursor-pointer flex-col rounded-[0.9rem] px-3 py-2", i === active && "bg-sunken")}
            >
              <span className="text-base text-ink">{option.name}</span>
              <span className="text-sm text-muted">{option.categoryName}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
