import { cn } from "@/lib/cn";

export function Meter({
  used,
  max,
  label,
  className,
}: {
  used: number;
  max: number;
  label: string;
  className?: string;
}) {
  const ratio = max <= 0 ? 1 : Math.min(1, Math.max(0, used / max));
  const pct = Math.round(ratio * 100);
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.min(used, max)}
      aria-valuetext={`${pct}% used`}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-line", className)}
    >
      <div
        className={cn("h-full rounded-full transition-[width] duration-500", ratio >= 0.85 ? "bg-caution" : "bg-ink")}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
