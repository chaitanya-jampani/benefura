import { MessageCircle } from "lucide-react";
import Link from "next/link";

import { Orb } from "@/components/ui";
import { cn } from "@/lib/cn";

export function AskLink({ question, label = "Ask about this", className }: { question: string; label?: string; className?: string }) {
  return (
    <Link
      href={`/chat?q=${encodeURIComponent(question)}`}
      className={cn("no-print inline-flex h-10 items-center gap-2.5 rounded-full pr-4 pl-1 text-base text-ink-2 hover:bg-surface", className)}
    >
      <Orb size={32}>
        <MessageCircle aria-hidden strokeWidth={2} className="size-4" />
      </Orb>
      {label}
    </Link>
  );
}
