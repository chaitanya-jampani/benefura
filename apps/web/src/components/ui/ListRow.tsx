import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { IconTile } from "./IconTile";

export function ListRow({
  icon,
  title,
  subtitle,
  trailing,
  href,
  onClick,
  children,
  className,
}: {
  icon: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  trailing?: ReactNode;
  href?: string;
  onClick?: () => void;
  children?: ReactNode;
  className?: string;
}) {
  const body = (
    <>
      <IconTile>{icon}</IconTile>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="truncate text-lg font-medium text-ink sm:text-xl">{title}</span>
        {subtitle && <span className="truncate text-base text-muted sm:text-lg">{subtitle}</span>}
        {children}
      </span>
      {trailing && <span className="shrink-0 text-right text-lg sm:text-xl">{trailing}</span>}
    </>
  );
  const rowClass = cn(
    "flex w-full items-center gap-4 rounded-tile py-3 text-left sm:gap-6",
    (href || onClick) && "-mx-3 px-3 hover:bg-sunken/70",
    className,
  );
  if (href) {
    return (
      <Link href={href} className={rowClass}>
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={rowClass}>
        {body}
      </button>
    );
  }
  return <div className={rowClass}>{body}</div>;
}
