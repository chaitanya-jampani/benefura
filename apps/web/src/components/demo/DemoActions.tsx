"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { buttonStyles } from "@/components/ui";
import { loadDemo } from "@/db/demo-seed";
import type { Region } from "@/domain/types";
import { cn } from "@/lib/cn";

const LABEL: Record<Region, string> = { CA: "Try the Canadian demo", AU: "Try the Australian demo" };

export function DemoActions({ className, showBooklet = true }: { className?: string; showBooklet?: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState<Region | null>(null);
  const [error, setError] = useState<string | null>(null);

  const start = async (region: Region) => {
    setBusy(region);
    setError(null);
    try {
      await loadDemo(region);
      router.push("/plan");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The demo couldn't be loaded in this browser.");
      setBusy(null);
    }
  };

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div className="flex flex-wrap gap-3">
        {(["CA", "AU"] as const).map((region, i) => (
          <button
            key={region}
            type="button"
            onClick={() => start(region)}
            disabled={busy !== null}
            aria-busy={busy === region}
            className={buttonStyles({ variant: i === 0 ? "primary" : "soft", size: "lg", className: "max-sm:w-full" })}
          >
            {busy === region ? "Loading demo" : LABEL[region]}
          </button>
        ))}
        {showBooklet && (
          <Link href="/redact" className={buttonStyles({ variant: "ghost", size: "lg", className: "max-sm:w-full" })}>
            Read my booklet
            <ArrowRight aria-hidden strokeWidth={1.5} className="size-5" />
          </Link>
        )}
      </div>
      {error && (
        <p role="alert" className="text-base text-negative">
          {error}
        </p>
      )}
    </div>
  );
}
