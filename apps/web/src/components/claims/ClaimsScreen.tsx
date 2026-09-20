"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { DashboardSkeleton, EmptyPlan } from "@/components/dashboard/DashboardStates";
import { buttonStyles, Surface } from "@/components/ui";
import { SETTING_KEYS } from "@/db/dexie";
import { useActivePlan, useClaims, useSetting, useToday } from "@/db/hooks";

import { ClaimDetail } from "./ClaimDetail";
import { ClaimEditor } from "./ClaimEditor";
import { ClaimsList } from "./ClaimsList";

export function ClaimsScreen() {
  const params = useSearchParams();
  const id = params.get("id");
  const record = useActivePlan();
  const claims = useClaims(record?.id);
  const includeSubmitted = useSetting<boolean>(SETTING_KEYS.includeSubmittedClaims, false);
  const today = useToday();

  if (record === undefined || includeSubmitted === undefined || (record && claims === undefined)) return <DashboardSkeleton />;
  if (record === null) return <EmptyPlan message="Claims are tracked against a plan. Open a demo plan or read your booklet first." />;
  const all = claims ?? [];

  if (id === "new") {
    return (
      <ClaimEditor
        key={`new-${params.get("member")}-${params.get("benefit")}-${params.get("receipt")}`}
        record={record}
        claims={all}
        claim={null}
        includeSubmitted={includeSubmitted}
        today={today}
        initialMember={params.get("member")}
        initialBenefit={params.get("benefit")}
        openReceipt={params.get("receipt") === "1"}
      />
    );
  }
  if (id) {
    const claim = all.find((c) => c.id === id);
    if (!claim) {
      return (
        <Surface className="max-w-2xl">
          <h1 className="text-3xl font-medium tracking-tight text-ink">Claim not found</h1>
          <p className="mt-3 text-lg text-ink-2">It may have been deleted, or it belongs to another plan in this browser.</p>
          <Link href="/claims" className={buttonStyles({ variant: "primary", className: "mt-6" })}>
            All claims
          </Link>
        </Surface>
      );
    }
    if (claim.status === "draft") {
      return <ClaimEditor key={claim.id} record={record} claims={all} claim={claim} includeSubmitted={includeSubmitted} today={today} />;
    }
    return <ClaimDetail key={claim.id} record={record} claims={all} claim={claim} includeSubmitted={includeSubmitted} today={today} />;
  }
  return <ClaimsList record={record} claims={all} today={today} />;
}
