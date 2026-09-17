"use client";

import { House, MessageCircle, ReceiptText, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Orb } from "@/components/ui";
import { cn } from "@/lib/cn";

const ITEMS = [
  { href: "/plan", label: "Plan", Icon: House },
  { href: "/claims", label: "Claims", Icon: ReceiptText },
  { href: "/chat", label: "Ask Benefura", Icon: MessageCircle, orb: true },
  { href: "/privacy", label: "Privacy", Icon: ShieldCheck },
  { href: "/settings", label: "Settings", Icon: SlidersHorizontal },
] as const;

export function Dock() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Main"
      className="no-print fixed inset-x-0 bottom-0 z-40 px-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
    >
      <ul className="mx-auto flex h-24 max-w-md items-center justify-between rounded-shell bg-surface/90 px-6 shadow-float backdrop-blur-xl">
        {ITEMS.map(({ href, label, Icon, ...rest }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          if ("orb" in rest) {
            return (
              <li key={href}>
                <Link href={href} aria-label={label} aria-current={active ? "page" : undefined} className="block rounded-full">
                  <Orb size={68} className="motion-safe:animate-orb-breathe transition-transform active:scale-95">
                    <Icon aria-hidden className="size-7" strokeWidth={2} />
                  </Orb>
                </Link>
              </li>
            );
          }
          return (
            <li key={href}>
              <Link
                href={href}
                aria-label={label}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "grid size-12 place-items-center rounded-full transition-colors",
                  active ? "text-ink" : "text-faint hover:text-muted",
                )}
              >
                <Icon aria-hidden className="size-7" strokeWidth={1.5} />
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
