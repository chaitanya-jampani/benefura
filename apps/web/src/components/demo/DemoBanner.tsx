"use client";

import { RotateCcw } from "lucide-react";
import { useState } from "react";

import { loadDemo } from "@/db/demo-seed";
import type { Region } from "@/domain/types";
import { cn } from "@/lib/cn";

export function DemoBanner({ region, className }: { region: Region; className?: string }) {
  const [busy, setBusy] = useState(false);
  const reset = async () => {
    setBusy(true);
    try {
      await loadDemo(region);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      role="note"
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-tile bg-surface/70 px-5 py-3 text-base text-ink-2 shadow-tile",
        className,
      )}
    >
      <p className="flex items-center gap-2.5">
        <span aria-hidden className="size-2 shrink-0 rounded-full bg-caution" />
        <span>
          <span className="font-medium text-ink">Fictional data only.</span> The plan, members and claims are made up.
        </span>
      </p>
      <button
        type="button"
        onClick={reset}
        disabled={busy}
        className="no-print inline-flex h-9 items-center gap-2 rounded-full px-3 text-sm text-ink-2 hover:bg-sunken disabled:opacity-40"
      >
        <RotateCcw aria-hidden strokeWidth={1.5} className="size-4" />
        {busy ? "Resetting" : "Reset demo"}
      </button>
    </div>
  );
}
