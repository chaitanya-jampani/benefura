"use client";

import { ChevronRight } from "lucide-react";

import { BenefitGlyph } from "@/components/dashboard/BenefitIcon";
import { ListRow, Surface } from "@/components/ui";
import { useActivePlan, useClaims } from "@/db/hooks";

export function ContinuePlan() {
  const record = useActivePlan();
  const claims = useClaims(record?.id);
  if (!record) return null;
  const drafts = claims?.filter((c) => c.status === "draft").length ?? 0;
  return (
    <Surface aria-label="Your plan" className="py-4 sm:px-8 sm:py-5">
      <ListRow
        href="/plan"
        icon={<BenefitGlyph benefit={null} kind="other" />}
        title={record.plan.insurer}
        subtitle={`${record.isDemo ? "Demo plan" : "Your plan"}${drafts ? `, ${drafts} draft claim${drafts === 1 ? "" : "s"}` : ""}`}
        trailing={
          <span className="inline-flex items-center gap-1 text-base text-ink-2">
            Open
            <ChevronRight aria-hidden strokeWidth={1.5} className="size-5" />
          </span>
        }
      />
    </Surface>
  );
}
