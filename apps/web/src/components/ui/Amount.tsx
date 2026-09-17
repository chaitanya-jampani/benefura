import type { Currency } from "@/domain/types";
import { cn } from "@/lib/cn";
import { moneyParts } from "@/lib/format";

export function HeroAmount({
  cents,
  currency,
  showCents = false,
  className,
}: {
  cents: number;
  currency: Currency;
  showCents?: boolean;
  className?: string;
}) {
  const p = moneyParts(cents, currency);
  return (
    <span className={cn("inline-flex items-baseline gap-2 font-light text-ink", className)}>
      <span className="text-2xl sm:text-3xl">
        {p.sign}
        {p.symbol}
      </span>
      <span className="text-hero">{p.whole}</span>
      {showCents && <span className="text-2xl text-muted sm:text-3xl">.{p.cents}</span>}
    </span>
  );
}

export function Amount({
  cents,
  currency,
  whole,
  className,
}: {
  cents: number;
  currency: Currency;
  whole?: boolean;
  className?: string;
}) {
  const p = moneyParts(cents, currency);
  return (
    <span className={cn("font-medium whitespace-nowrap", className)}>
      {p.sign}
      {p.symbol}
      {p.whole}
      {whole ? null : `.${p.cents}`}
    </span>
  );
}
