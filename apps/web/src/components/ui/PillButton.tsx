import Link from "next/link";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "@/lib/cn";

interface PillButtonBase {
  icon: ReactNode;
  label: string;
  showLabel?: boolean;
  className?: string;
}

const pill =
  "pill-soft grid h-[4.5rem] w-full place-items-center rounded-full text-ink transition-transform duration-150 active:scale-[0.97] disabled:opacity-40";

export function PillButton({
  icon,
  label,
  showLabel = true,
  className,
  ...rest
}: PillButtonBase & Omit<ComponentPropsWithoutRef<"button">, "children">) {
  return (
    <div className={cn("flex min-w-0 flex-col items-center gap-2", className)}>
      <button type="button" aria-label={label} className={pill} {...rest}>
        {icon}
      </button>
      {showLabel && <span className="text-sm text-muted">{label}</span>}
    </div>
  );
}

export function PillLink({
  icon,
  label,
  showLabel = true,
  className,
  href,
}: PillButtonBase & { href: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col items-center gap-2", className)}>
      <Link href={href} aria-label={label} className={pill}>
        {icon}
      </Link>
      {showLabel && <span className="text-sm text-muted">{label}</span>}
    </div>
  );
}
