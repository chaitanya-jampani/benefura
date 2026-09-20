import { afterEach, describe, expect, it } from "vitest";

import { daysBetween } from "@/domain/periods";
import { computeUsage } from "@/domain/usage";

import { BenefuraDB, SETTING_KEYS } from "../dexie";
import { buildDemo, clearDemo, DEMO_PLAN_IDS, loadDemo } from "../demo-seed";
import { deleteAllData, exportData, resetDemo } from "../maintenance";

const TODAY = "2026-09-16";
const NOW = new Date("2026-09-16T12:00:00");

let n = 0;
const dbs: BenefuraDB[] = [];
function freshDb() {
  const database = new BenefuraDB(`test-${Date.now()}-${n++}`);
  dbs.push(database);
  return database;
}

afterEach(async () => {
  for (const database of dbs.splice(0)) {
    database.close();
    await database.delete();
  }
});

describe("buildDemo", () => {
  it("builds a Canadian history with every status and interesting meters", () => {
    const demo = buildDemo("CA", TODAY, NOW);
    expect(demo.plan).toMatchObject({ id: "demo-ca-northwind", isDemo: true });
    expect(demo.plan.plan.document?.isDemo).toBe(true);
    expect(new Set(demo.claims.map((c) => c.status))).toEqual(new Set(["draft", "submitted", "paid", "partially_paid", "rejected"]));
    for (const claim of demo.claims) {
      for (const line of claim.lines) expect(line.serviceDate <= TODAY).toBe(true);
    }

    const usage = computeUsage(demo.plan.plan, demo.claims, { today: TODAY, includeSubmitted: false });
    const a = usage.members.find((m) => m.memberId === "m-a")!;
    const massage = a.benefits.find((b) => b.benefitId === "ben-massage")!;
    expect(massage.limits[0]).toMatchObject({ used: 40000, remaining: 10000 });
    const withSubmitted = computeUsage(demo.plan.plan, demo.claims, { today: TODAY, includeSubmitted: true });
    expect(withSubmitted.members[0].benefits.find((b) => b.benefitId === "ben-massage")!.limits[0].remaining).toBe(2000);

    const b = usage.members.find((m) => m.memberId === "m-b")!;
    const eyewear = b.benefits.find((x) => x.benefitId === "ben-eyewear")!;
    expect(eyewear.limits[0]).toMatchObject({ used: 26000, remaining: 4000 });
    expect(usage.family.costShares[0]).toMatchObject({ metCents: 2500, remainingCents: 0 });

    const c = usage.members.find((m) => m.memberId === "m-c")!;
    expect(c.benefits.find((x) => x.benefitId === "ben-eye-exam")!.frequency?.nextEligible).not.toBeNull();

    const drafted = demo.claims.find((cl) => cl.history[0].actor === "agent")!;
    expect(drafted.history[0]).toMatchObject({ agentName: "benefura-plan-claims", traceId: expect.any(String) });
    expect(demo.receipts.length).toBeGreaterThan(0);
    expect(demo.receipts.every((r) => demo.claims.some((cl) => cl.attachments.some((at) => at.receiptId === r.id)))).toBe(true);
    expect(demo.aliases.map((al) => al.token)).toEqual(["[MEMBER_A]", "[MEMBER_B]", "[MEMBER_C]", "[POLICY_1]"]);
  });

  it("builds an Australian history near the therapies limit with a draft close to its deadline", () => {
    const demo = buildDemo("AU", TODAY, NOW);
    const usage = computeUsage(demo.plan.plan, demo.claims, { today: TODAY, includeSubmitted: false });
    const pool = usage.members[0].pools.find((p) => p.poolId === "pool-therapies")!;
    expect(pool.used).toBe(63500);
    expect(pool.used / pool.limit.value).toBeGreaterThan(0.85);
    const optical = usage.members[1].benefits.find((x) => x.benefitId === "ben-optical")!;
    expect(optical.remainingCents).toBe(0);
    const urgent = demo.claims.filter((cl) => cl.status === "draft" && cl.deadline && daysBetween(TODAY, cl.deadline) <= 30);
    expect(urgent).toHaveLength(1);
  });

  it("stays valid on any day of the year", () => {
    for (const today of ["2026-01-01", "2026-01-20", "2026-03-10", "2026-06-30", "2026-12-31", "2027-02-28"]) {
      for (const region of ["CA", "AU"] as const) {
        const demo = buildDemo(region, today, new Date(`${today}T12:00:00`));
        for (const claim of demo.claims) {
          for (const line of claim.lines) {
            expect(line.serviceDate <= today).toBe(true);
            expect(line.serviceDate >= (demo.plan.plan.effectiveDate ?? "")).toBe(true);
          }
        }
      }
    }
  });
});

describe("demo storage and maintenance", () => {
  it("loads, reloads and clears a demo", async () => {
    const database = freshDb();
    await loadDemo("CA", { today: TODAY, now: NOW, database });
    expect(await database.plans.count()).toBe(1);
    const claimCount = await database.claims.count();
    expect(claimCount).toBeGreaterThan(10);
    expect((await database.settings.get(SETTING_KEYS.activePlanId))?.value).toBe(DEMO_PLAN_IDS.CA);

    await loadDemo("AU", { today: TODAY, now: NOW, database });
    await loadDemo("CA", { today: TODAY, now: NOW, database });
    expect(await database.plans.count()).toBe(2);
    expect(await database.claims.where("planId").equals(DEMO_PLAN_IDS.CA).count()).toBe(claimCount);

    await clearDemo("CA", database);
    expect(await database.plans.get(DEMO_PLAN_IDS.CA)).toBeUndefined();
    expect(await database.claims.where("planId").equals(DEMO_PLAN_IDS.CA).count()).toBe(0);
    expect(await database.aliases.filter((a) => a.id.startsWith("demo-ca-")).count()).toBe(0);
    expect(await database.settings.get(SETTING_KEYS.activePlanId)).toBeUndefined();
  });

  it("resets loaded demos, keeping the active plan", async () => {
    const database = freshDb();
    await loadDemo("AU", { today: TODAY, now: NOW, database });
    await loadDemo("CA", { today: TODAY, now: NOW, database });
    await database.settings.put({ key: SETTING_KEYS.activePlanId, value: DEMO_PLAN_IDS.AU });
    await database.claims.where("planId").equals(DEMO_PLAN_IDS.AU).delete();
    expect(await resetDemo(database)).toEqual(["CA", "AU"]);
    expect(await database.claims.where("planId").equals(DEMO_PLAN_IDS.AU).count()).toBeGreaterThan(5);
    expect((await database.settings.get(SETTING_KEYS.activePlanId))?.value).toBe(DEMO_PLAN_IDS.AU);
  });

  it("exports without aliases or images unless asked, and deletes everything", async () => {
    const database = freshDb();
    await loadDemo("CA", { today: TODAY, now: NOW, database });
    const receipt = (await database.receipts.toArray())[0];
    await database.receipts.put({ ...receipt, image: new Blob(["jpeg"], { type: "image/jpeg" }) });

    const plain = await exportData({ includeAliases: false }, database);
    expect(plain.format).toBe("benefura-export");
    expect(plain.plans).toHaveLength(1);
    expect(plain.claims.length).toBeGreaterThan(10);
    expect(plain.aliases).toBeUndefined();
    expect(plain.receipts.every((r) => !("image" in r))).toBe(true);
    expect(JSON.stringify(plain)).not.toContain("Jordan Tremblay");

    const withAliases = await exportData({ includeAliases: true }, database);
    expect(withAliases.aliases?.some((a) => a.values.includes("Jordan Tremblay"))).toBe(true);

    await database.outbound.add({ at: NOW.toISOString(), kind: "healthz", method: "GET", url: "x", bytes: 0, thumbnails: [] });
    await deleteAllData(database);
    for (const table of database.tables) expect(await table.count()).toBe(0);
  });
});
