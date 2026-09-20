"use client";

// Hooks return `undefined` while the first read is in flight and `null` when there is nothing to show.
import { useLiveQuery } from "dexie-react-hooks";
import { useState } from "react";

import { todayIso } from "@/domain/periods";
import type { Claim, IsoDate } from "@/domain/types";

import { db, getSetting, setSetting, SETTING_KEYS, type PlanRecord, type ReceiptRecord } from "./dexie";

// Fixed for the life of the screen.
export function useToday(): IsoDate {
  const [today] = useState(() => todayIso());
  return today;
}

export function usePlans(): PlanRecord[] | undefined {
  return useLiveQuery(() => db.plans.orderBy("updatedAt").reverse().toArray(), []);
}

export function useActivePlan(): PlanRecord | null | undefined {
  return useLiveQuery(async () => {
    const id = await getSetting<string | null>(SETTING_KEYS.activePlanId, null);
    const chosen = id ? await db.plans.get(id) : undefined;
    if (chosen) return chosen;
    return (await db.plans.orderBy("updatedAt").reverse().first()) ?? null;
  }, []);
}

export function useClaims(planId: string | null | undefined): Claim[] | undefined {
  // Tagged with the plan id so a plan switch never shows the previous plan's claims.
  const result = useLiveQuery(
    async () => ({ planId, claims: planId ? await db.claims.where("planId").equals(planId).toArray() : [] }),
    [planId],
  );
  return result && result.planId === planId ? result.claims : undefined;
}

export function useClaim(id: string | null | undefined): Claim | null | undefined {
  return useLiveQuery(async () => (id ? ((await db.claims.get(id)) ?? null) : null), [id]);
}

export function useReceipts(ids: string[]): ReceiptRecord[] | undefined {
  const key = ids.join(",");
  return useLiveQuery(async () => (key ? (await db.receipts.bulkGet(key.split(","))).filter((r): r is ReceiptRecord => !!r) : []), [key]);
}

export function useSetting<T>(key: string, fallback: T): T | undefined {
  return useLiveQuery(() => getSetting<T>(key, fallback), [key]);
}

export function setActivePlan(planId: string): Promise<void> {
  return setSetting(SETTING_KEYS.activePlanId, planId);
}

export async function saveClaim(claim: Claim): Promise<void> {
  await db.claims.put(claim);
}

export async function deleteClaim(id: string): Promise<void> {
  await db.transaction("rw", [db.claims, db.receipts], async () => {
    await db.claims.delete(id);
    await db.receipts.where("claimId").equals(id).modify((r) => {
      delete r.claimId;
    });
  });
}
