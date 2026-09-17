import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function IconTile({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "grid size-14 shrink-0 place-items-center rounded-tile bg-surface text-ink shadow-tile sm:size-16",
        className,
      )}
    >
      {children}
    </span>
  );
}
