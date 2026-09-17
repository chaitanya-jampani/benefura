import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Chip({
  children,
  tone = "muted",
  className,
}: {
  children: ReactNode;
  tone?: "muted" | "positive" | "caution" | "negative" | "assistant";
  className?: string;
}) {
  const dot = {
    muted: "bg-faint",
    positive: "bg-positive",
    caution: "bg-caution",
    negative: "bg-negative",
    assistant: "orb",
  }[tone];
  return (
    <span
      className={cn(
        "inline-flex h-8 items-center gap-2 rounded-full bg-surface px-3 text-sm text-ink-2 shadow-tile",
        className,
      )}
    >
      <span aria-hidden className={cn("size-2 rounded-full", dot)} />
      {children}
    </span>
  );
}
