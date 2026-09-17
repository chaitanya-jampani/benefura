import { cn } from "@/lib/cn";

export function Trend({
  value,
  caption,
  tone = "positive",
  className,
}: {
  value: string;
  caption?: string;
  tone?: "positive" | "caution" | "negative" | "muted";
  className?: string;
}) {
  const color = {
    positive: "text-positive",
    caution: "text-caution",
    negative: "text-negative",
    muted: "text-muted",
  }[tone];
  return (
    <span className={cn("inline-flex flex-col leading-tight", className)}>
      <span className={cn("inline-flex items-center gap-1.5 text-base", color)}>
        {tone !== "muted" && (
          <svg viewBox="0 0 10 8" className="size-2.5" aria-hidden>
            <path d="M5 0.6 9.4 7.4H0.6Z" fill="currentColor" transform={tone === "positive" ? undefined : "rotate(180 5 4)"} />
          </svg>
        )}
        {value}
      </span>
      {caption && <span className="text-base text-muted">{caption}</span>}
    </span>
  );
}
