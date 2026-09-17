import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentPropsWithoutRef } from "react";

import { cn } from "@/lib/cn";

export const buttonStyles = cva(
  "inline-flex items-center justify-center gap-2 rounded-full font-medium whitespace-nowrap transition-[transform,background-color,opacity] duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40",
  {
    variants: {
      variant: {
        primary: "bg-ink text-white hover:bg-ink-2",
        soft: "pill-soft text-ink",
        ghost: "text-ink-2 hover:bg-sunken",
        danger: "bg-surface text-negative ring-1 ring-negative/30 hover:bg-negative/5",
      },
      size: {
        sm: "h-9 px-4 text-sm",
        md: "h-12 px-6 text-base",
        lg: "h-14 px-8 text-lg",
      },
    },
    defaultVariants: { variant: "soft", size: "md" },
  },
);

export type ButtonProps = ComponentPropsWithoutRef<"button"> & VariantProps<typeof buttonStyles>;

export function Button({ className, variant, size, type = "button", ...rest }: ButtonProps) {
  return <button type={type} className={cn(buttonStyles({ variant, size }), className)} {...rest} />;
}
