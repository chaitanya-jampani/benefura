"use client";

import { useMemo, useState } from "react";

import { DemoBanner } from "@/components/demo/DemoBanner";
import { SelectInput } from "@/components/ui/Field";
import { Switch } from "@/components/ui/Switch";
import { setSetting, SETTING_KEYS, type PlanRecord } from "@/db/dexie";
import { setActivePlan, useActivePlan, useClaims, usePlans, useSetting, useToday } from "@/db/hooks";
import { currentBenefitPeriod } from "@/domain/periods";
import { computeUsage, memberHeadline } from "@/domain/usage";
import type { Claim } from "@/domain/types";

import { BenefitList } from "./BenefitList";
import { DashboardSkeleton, EmptyPlan } from "./DashboardStates";
import { HeroCard } from "./HeroCard";
import { benefitRows } from "./model";
import { CostSharesCard, DeadlinesCard, FrequencyCard, HospitalCard, HsaCard, PoolsCard, WaitingPeriodsCard } from "./SideCards";

export function PlanDashboard() {
  const record = useActivePlan();
  const plans = usePlans();
  const claims = useClaims(record?.id);
  const includeSubmitted = useSetting<boolean>(SETTING_KEYS.includeSubmittedClaims, false);
  const today = useToday();

  if (record === undefined || includeSubmitted === undefined || (record && claims === undefined)) return <DashboardSkeleton />;
  if (record === null) return <EmptyPlan />;
  return (
    <Dashboard
      key={record.id}
      record={record}
      plans={plans ?? [record]}
      claims={claims ?? []}
      includeSubmitted={includeSubmitted}
      today={today}
    />
  );
}

function Dashboard({
  record,
  plans,
  claims,
  includeSubmitted,
  today,
}: {
  record: PlanRecord;
  plans: PlanRecord[];
  claims: Claim[];
  includeSubmitted: boolean;
  today: string;
}) {
  // An extraction that found no alias rows saves a plan without members.
  const plan = useMemo(
    () =>
      record.plan.members.length > 0
        ? record.plan
        : { ...record.plan, members: [{ id: "m-a", alias: "[MEMBER_A]", relationship: "self" as const }] },
    [record.plan],
  );
  const [memberId, setMemberId] = useState(() => (plan.members.find((m) => m.relationship === "self") ?? plan.members[0])?.id ?? "");
  const usage = useMemo(() => computeUsage(plan, claims, { today, includeSubmitted }), [plan, claims, today, includeSubmitted]);
  const member = plan.members.find((m) => m.id === memberId) ?? plan.members[0];
  const memberUsage = usage.members.find((m) => m.memberId === member?.id);
  const year = currentBenefitPeriod(plan, today);

  if (!member || !memberUsage) {
    return <EmptyPlan />;
  }

  const used = memberHeadline(plan, memberUsage, "used");
  const scope = used.usedBenefitIds.length > 0 ? "used" : "all";
  const headline = scope === "used" ? used : memberHeadline(plan, memberUsage, "all");
  const rows = benefitRows(plan, memberUsage);

  return (
    <div className="space-y-6">
      {record.isDemo && <DemoBanner region={plan.region} />}

      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4 px-1">
        <div className="min-w-0">
          <p className="text-base text-muted">{plan.insurer}</p>
          <h1 className="text-3xl font-medium tracking-tight text-ink sm:text-4xl">{plan.planName}</h1>
        </div>
        {plans.length > 1 && (
          <div className="w-full sm:w-72">
            <label htmlFor="plan-switch" className="sr-only">
              Plan
            </label>
            <SelectInput id="plan-switch" value={record.id} onChange={(e) => setActivePlan(e.target.value)}>
              {plans.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.plan.insurer}
                  {p.isDemo ? " (demo)" : ""}
                </option>
              ))}
            </SelectInput>
          </div>
        )}
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <HeroCard plan={plan} member={member} onMember={setMemberId} headline={headline} scope={scope} year={year} />
          <div className="rounded-tile bg-surface/60 px-5 py-4">
            <Switch
              checked={includeSubmitted}
              onChange={(value) => setSetting(SETTING_KEYS.includeSubmittedClaims, value)}
              label="Count submitted claims"
              description="Treat claims awaiting a decision as paid at their estimate."
            />
          </div>
          <BenefitList plan={plan} member={member} rows={rows} today={today} />
          <div className="hidden lg:block">
            <HsaCard plan={plan} claims={claims} member={member} today={today} />
          </div>
        </div>
        <div className="space-y-6">
          <DeadlinesCard plan={plan} claims={claims} today={today} />
          <PoolsCard plan={plan} member={memberUsage} />
          <FrequencyCard plan={plan} member={memberUsage} />
          <CostSharesCard plan={plan} usage={usage} member={memberUsage} today={today} />
          <HospitalCard plan={plan} />
          <WaitingPeriodsCard plan={plan} member={memberUsage} />
          <div className="lg:hidden">
            <HsaCard plan={plan} claims={claims} member={member} today={today} />
          </div>
        </div>
      </div>
    </div>
  );
}
