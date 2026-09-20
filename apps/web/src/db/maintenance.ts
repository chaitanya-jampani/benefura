import type { Claim, Region } from "@/domain/types";

import { db as defaultDb, SETTING_KEYS, type AliasRecord, type BenefuraDB, type PlanRecord, type ReceiptRecord } from "./dexie";
import { DEMO_PLAN_IDS, loadDemo } from "./demo-seed";

export const EXPORT_FORMAT = "benefura-export";
export const EXPORT_VERSION = 1;

export interface BenefuraExport {
  format: typeof EXPORT_FORMAT;
  version: typeof EXPORT_VERSION;
  exportedAt: string;
  plans: PlanRecord[];
  claims: Claim[];
  receipts: Array<Omit<ReceiptRecord, "image">>;
  /** Only when the user opts in: these hold the raw values behind each alias. */
  aliases?: AliasRecord[];
}

export async function resetDemo(database: BenefuraDB = defaultDb): Promise<Region[]> {
  const demos = await database.plans.filter((p) => p.isDemo).toArray();
  const active = await database.settings.get(SETTING_KEYS.activePlanId);
  const regions = (Object.keys(DEMO_PLAN_IDS) as Region[]).filter((r) => demos.some((p) => p.id === DEMO_PLAN_IDS[r]));
  for (const region of regions) await loadDemo(region, { database });
  if (active && regions.some((r) => DEMO_PLAN_IDS[r] === active.value)) {
    await database.settings.put(active);
  }
  return regions;
}

export async function deleteAllData(database: BenefuraDB = defaultDb): Promise<void> {
  await database.transaction("rw", database.tables, async () => {
    await Promise.all(database.tables.map((table) => table.clear()));
  });
}

export async function exportData(opts: { includeAliases: boolean }, database: BenefuraDB = defaultDb): Promise<BenefuraExport> {
  const [plans, claims, receipts, aliases] = await Promise.all([
    database.plans.toArray(),
    database.claims.toArray(),
    database.receipts.toArray(),
    opts.includeAliases ? database.aliases.toArray() : Promise.resolve(undefined),
  ]);
  return {
    format: EXPORT_FORMAT,
    version: EXPORT_VERSION,
    exportedAt: new Date().toISOString(),
    plans,
    claims,
    receipts: receipts.map(({ image, ...rest }) => {
      void image;
      return rest;
    }),
    ...(aliases ? { aliases } : {}),
  };
}

export function exportFileName(date = new Date()): string {
  return `benefura-export-${date.toISOString().slice(0, 10)}.json`;
}

export function downloadJson(data: unknown, fileName: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
