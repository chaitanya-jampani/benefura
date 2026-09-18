import auWattle from "@samples/fixtures/au-wattle.plan.json";
import caNorthwind from "@samples/fixtures/ca-northwind.plan.json";

import type { Plan, Region } from "./types";

export const CA_DEMO_PLAN = caNorthwind as unknown as Plan;
export const AU_DEMO_PLAN = auWattle as unknown as Plan;

export const DEMO_PLANS: Record<Region, Plan> = { CA: CA_DEMO_PLAN, AU: AU_DEMO_PLAN };
