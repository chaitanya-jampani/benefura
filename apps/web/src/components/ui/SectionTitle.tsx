import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function SectionTitle({
  children,
  action,
  as: Tag = "h2",
  className,
}: {
  children: ReactNode;
  action?: ReactNode;
  as?: "h1" | "h2" | "h3";
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-4", className)}>
      <Tag className="text-2xl font-medium tracking-tight text-ink sm:text-[1.75rem]">{children}</Tag>
      {action}
    </div>
  );
}
