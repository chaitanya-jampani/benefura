// Asserts every engine-exact number in evals/datasets/plan_claims_scenarios.jsonl with the real browser tools,
// so an engine change fails here before the evals drift.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

import { runBrowserTool } from "@/agent/tools";
import { db, SETTING_KEYS, setSetting } from "@/db/dexie";

import type { Claim, Plan } from "../types";

interface ExpectedToolOutput {
  tool: string;
  args: Record<string, unknown>;
  expect: Record<string, unknown>;
}

interface Scenario {
  id: string;
  plan: string;
  context: { today: string };
  executorOptions?: { includeSubmitted?: boolean };
  seededClaims: Claim[];
  expectedToolOutputs: ExpectedToolOutput[];
}

const repoRoot = fileURLToPath(new URL("../../../../../", import.meta.url));
const scenarios: Scenario[] = readFileSync(`${repoRoot}evals/datasets/plan_claims_scenarios.jsonl`, "utf8")
  .split("\n")
  .filter((line) => line.trim())
  .map((line) => JSON.parse(line) as Scenario);

function pathValues(value: unknown, path: string): unknown[] {
  let current: unknown[] = [value];
  for (const part of path.split(".")) {
    const next: unknown[] = [];
    for (const item of current) {
      if (part === "*" && Array.isArray(item)) next.push(...item);
      else if (Array.isArray(item) && /^\d+$/.test(part) && Number(part) < item.length) next.push(item[Number(part)]);
      else if (item !== null && typeof item === "object" && !Array.isArray(item) && part in item) {
        next.push((item as Record<string, unknown>)[part]);
      }
    }
    current = next;
  }
  return current;
}

async function seed(scenario: Scenario): Promise<void> {
  const plan = JSON.parse(readFileSync(`${repoRoot}${scenario.plan}`, "utf8")) as Plan;
  await Promise.all([db.plans.clear(), db.claims.clear(), db.settings.clear()]);
  await db.plans.put({ id: plan.id, plan, isDemo: true, createdAt: "2025-01-01T00:00:00Z", updatedAt: "2025-01-01T00:00:00Z" });
  await setSetting(SETTING_KEYS.activePlanId, plan.id);
  await setSetting(SETTING_KEYS.includeSubmittedClaims, scenario.executorOptions?.includeSubmitted ?? false);
  if (scenario.seededClaims.length) await db.claims.bulkPut(scenario.seededClaims);
}

beforeEach(async () => {
  await db.open();
});

afterAll(() => {
  vi.useRealTimers();
});

it("loads all 16 plan-and-claims scenarios", () => {
  expect(scenarios).toHaveLength(16);
  expect(scenarios.every((s) => s.expectedToolOutputs.length > 0)).toBe(true);
});

describe.each(scenarios)("$id", (scenario) => {
  it.each(scenario.expectedToolOutputs.map((expected, i) => ({ ...expected, i })))(
    "$tool #$i matches the engine",
    async ({ tool, args, expect: expectations }) => {
      await seed(scenario);
      const now = new Date(`${scenario.context.today}T12:00:00Z`);
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(now);
      try {
        const output = await runBrowserTool(tool, args, { now, tz: "UTC" });
        for (const [path, value] of Object.entries(expectations)) {
          const values = pathValues(output, path);
          if (path.split(".").includes("*")) {
            expect(values, `${scenario.id} ${tool} ${path}`).toContainEqual(value);
          } else {
            expect(values, `${scenario.id} ${tool} ${path}`).toEqual([value]);
          }
        }
      } finally {
        vi.useRealTimers();
      }
    },
  );
});
