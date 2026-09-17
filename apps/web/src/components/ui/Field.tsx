import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "@/lib/cn";

const control =
  "h-12 w-full min-w-0 rounded-full bg-sunken px-5 text-base text-ink ring-1 ring-transparent transition-[box-shadow,background-color] placeholder:text-faint hover:ring-line focus-visible:bg-surface focus-visible:ring-line disabled:opacity-50";

export function Field({
  label,
  htmlFor,
  hint,
  optional,
  children,
  className,
}: {
  label: ReactNode;
  htmlFor: string;
  hint?: ReactNode;
  optional?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      <label htmlFor={htmlFor} className="px-1 text-sm text-ink-2">
        {label}
        {optional && <span className="text-muted"> (optional)</span>}
      </label>
      {children}
      {hint && <p className="px-1 text-sm text-muted">{hint}</p>}
    </div>
  );
}

export function TextInput({ className, ...rest }: ComponentPropsWithoutRef<"input">) {
  return <input className={cn(control, className)} {...rest} />;
}

export function SelectInput({ className, children, ...rest }: ComponentPropsWithoutRef<"select">) {
  return (
    <select className={cn(control, "appearance-none bg-[length:1rem] bg-[right_1.25rem_center] bg-no-repeat pr-12", className)} style={{ backgroundImage: CHEVRON }} {...rest}>
      {children}
    </select>
  );
}

export function TextArea({ className, ...rest }: ComponentPropsWithoutRef<"textarea">) {
  return <textarea className={cn(control, "h-auto min-h-24 rounded-tile py-3", className)} {...rest} />;
}

const CHEVRON =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238e8e97' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")";
