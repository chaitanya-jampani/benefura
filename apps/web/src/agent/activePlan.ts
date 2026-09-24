import { db, getSetting, SETTING_KEYS, type PlanRecord } from "@/db/dexie";

export async function loadActivePlanRecord(): Promise<PlanRecord | null> {
  const activeId = await getSetting<string | null>(SETTING_KEYS.activePlanId, null);
  const record = (activeId ? await db.plans.get(activeId) : undefined) ?? (await db.plans.orderBy("updatedAt").last());
  return record ?? null;
}
