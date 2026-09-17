import type { ComponentPropsWithoutRef, ElementType } from "react";

import { cn } from "@/lib/cn";

type SurfaceProps<T extends ElementType> = {
  as?: T;
  glow?: boolean;
} & ComponentPropsWithoutRef<T>;

export function Surface<T extends ElementType = "section">({ as, glow, className, ...rest }: SurfaceProps<T>) {
  const Comp = (as ?? "section") as ElementType;
  return (
    <Comp
      className={cn(
        "relative rounded-shell shadow-float",
        glow ? "glow-corner" : "bg-surface",
        "p-6 sm:p-10",
        className,
      )}
      {...rest}
    />
  );
}
