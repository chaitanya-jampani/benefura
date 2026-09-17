import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** Reserved for the assistant. */
export function Orb({ size = 76, className, children }: { size?: number; className?: string; children?: ReactNode }) {
  return (
    <span
      aria-hidden={children ? undefined : true}
      className={cn("orb inline-grid shrink-0 place-items-center rounded-full text-white", className)}
      style={{ width: size, height: size }}
    >
      {children}
    </span>
  );
}
