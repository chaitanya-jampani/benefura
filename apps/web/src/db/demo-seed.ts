// Claims are dated relative to today and paid amounts come from the engine, so meters and estimates agree.
import { createDraftClaim, updateClaim, USER_ACTOR, type ClaimActor } from "@/domain/claims";
import { estimateClaim } from "@/domain/estimate";
import { DEMO_PLANS } from "@/domain/fixtures";
import { indexPlan } from "@/domain/ledger";
import { addDays, currentBenefitPeriod, daysBetween, minDate, periodWindow, todayIso, waitingPeriodEnds } from "@/domain/periods";
import type { Claim, IsoDate, Plan, Region } from "@/domain/types";

import { db as defaultDb, SETTING_KEYS, type AliasRecord, type BenefuraDB, type PlanRecord, type ReceiptRecord } from "./dexie";

export const DEMO_PLAN_IDS: Record<Region, string> = { CA: DEMO_PLANS.CA.id, AU: DEMO_PLANS.AU.id };

export const DEMO_AGENT: ClaimActor = {
  actor: "agent",
  agentName: "benefura-plan-claims",
  agentVersion: "3",
  traceId: "4bf92f3577b34da6a3ce929d0e0e4736",
};

type When = { frac: number } | { daysAgo: number } | { windowOf: string; frac: number };

interface LineSpec {
  benefitId: string;
  when: When;
  charged: number;
  quantity?: number;
  itemCode?: string;
  description: string;
}

interface ClaimSpec {
  member: string;
  provider: string;
  providerType: string;
  lines: LineSpec[];
  status: Claim["status"];
  /** Share of the engine estimate paid, for partially paid claims. */
  paidShare?: number;
  note?: string;
  byAgent?: boolean;
  receipt?: boolean;
}

const line = (benefitId: string, when: When, charged: number, description: string, extra: Partial<LineSpec> = {}): LineSpec => ({
  benefitId,
  when,
  charged,
  description,
  ...extra,
});

const f = (frac: number): When => ({ frac });

function caClaims(today: IsoDate, plan: Plan): ClaimSpec[] {
  const year = currentBenefitPeriod(plan, today);
  const inGrace = daysBetween(year.start, today) < 75;
  return [
    ...[0.08, 0.22, 0.38, 0.55, 0.72].map<ClaimSpec>((frac, i) => ({
      member: "m-a",
      provider: "Harbour Massage Therapy",
      providerType: "Registered massage therapist",
      lines: [line("ben-massage", f(frac), 12000, "Massage therapy, 60 minutes")],
      status: "paid",
      receipt: i === 4,
    })),
    {
      member: "m-a",
      provider: "Lakeshore Physiotherapy",
      providerType: "Physiotherapist",
      lines: [0.3, 0.34, 0.4].map((frac) => line("ben-physio", f(frac), 9500, "Physiotherapy treatment")),
      status: "paid",
      receipt: true,
    },
    {
      member: "m-a",
      provider: "Queen Street Dental",
      providerType: "Dentist",
      lines: [line("ben-dental-recall", f(0.45), 24000, "Recall exam and cleaning", { itemCode: "01202" })],
      status: "paid",
    },
    { member: "m-a", provider: "Maple Pharmacy", providerType: "Pharmacy", lines: [line("ben-drugs", f(0.15), 4250, "Prescription refill")], status: "paid" },
    { member: "m-a", provider: "Maple Pharmacy", providerType: "Pharmacy", lines: [line("ben-drugs", f(0.6), 6000, "Prescription refill")], status: "paid" },
    {
      member: "m-a",
      provider: "Harbour Massage Therapy",
      providerType: "Registered massage therapist",
      lines: [line("ben-massage", f(0.9), 12000, "Massage therapy, 60 minutes")],
      status: "submitted",
    },
    {
      member: "m-a",
      provider: "Summit Chiropractic",
      providerType: "Chiropractor",
      lines: [line("ben-chiro", f(0.97), 7500, "Chiropractic adjustment")],
      status: "draft",
      byAgent: true,
    },
    {
      member: "m-a",
      provider: "Stride Foot Clinic",
      providerType: "Chiropodist",
      lines: [line("ben-orthotics", inGrace ? { daysAgo: daysBetween(addDays(year.start, -12), today) } : f(0.93), 48000, "Custom foot orthotics, one pair")],
      status: "draft",
    },
    {
      member: "m-b",
      provider: "Clearview Optical",
      providerType: "Optician",
      lines: [line("ben-eyewear", { windowOf: "ben-eyewear", frac: 0.55 }, 26000, "Prescription glasses")],
      status: "paid",
      receipt: true,
    },
    {
      member: "m-b",
      provider: "Clearwater Psychology",
      providerType: "Psychologist",
      lines: [0.2, 0.35, 0.5, 0.65].map((frac) => line("ben-mental-health", f(frac), 18000, "Individual session, 50 minutes")),
      status: "paid",
    },
    {
      member: "m-b",
      provider: "Queen Street Dental",
      providerType: "Dentist",
      lines: [line("ben-dental-major", f(0.5), 115000, "Porcelain crown", { itemCode: "27201" })],
      status: "partially_paid",
      paidShare: 0.87,
      note: "Paid up to the dental fee guide",
    },
    {
      member: "m-b",
      provider: "Green Leaf Naturopathic",
      providerType: "Naturopath",
      lines: [line("ben-naturopath", f(0.4), 6500, "Herbal supplements")],
      status: "rejected",
      note: "Remedies and supplements are not covered",
    },
    {
      member: "m-c",
      provider: "Queen Street Dental",
      providerType: "Dentist",
      lines: [line("ben-dental-recall", f(0.35), 18000, "Recall exam and cleaning", { itemCode: "01202" })],
      status: "paid",
    },
    {
      member: "m-c",
      provider: "Queen Street Dental",
      providerType: "Dentist",
      lines: [line("ben-dental-basic", f(0.85), 21000, "Filling, two surfaces", { itemCode: "21211" })],
      status: "submitted",
    },
    {
      member: "m-c",
      provider: "Clearview Optical",
      providerType: "Optometrist",
      lines: [line("ben-eye-exam", { daysAgo: 425 }, 12000, "Eye examination")],
      status: "paid",
    },
  ];
}

function auClaims(): ClaimSpec[] {
  return [
    {
      member: "m-a",
      provider: "Bondi Physio Co",
      providerType: "Physiotherapist",
      lines: [
        line("ben-physio", f(0.1), 9500, "Initial consultation", { itemCode: "500" }),
        ...[0.18, 0.26, 0.34, 0.42, 0.5, 0.58].map((frac) => line("ben-physio", f(frac), 8500, "Subsequent consultation", { itemCode: "505" })),
      ],
      status: "paid",
      receipt: true,
    },
    ...[0.2, 0.45, 0.6, 0.75].map<ClaimSpec>((frac) => ({
      member: "m-a",
      provider: "Eastside Remedial Massage",
      providerType: "Remedial massage therapist",
      lines: [line("ben-remedial-massage", f(frac), 9000, "Remedial massage, 60 minutes", { itemCode: "205" })],
      status: "paid",
    })),
    {
      member: "m-a",
      provider: "Harbourside Chiropractic",
      providerType: "Chiropractor",
      lines: [
        line("ben-chiro", f(0.05), 8000, "Initial consultation", { itemCode: "1500" }),
        ...[0.3, 0.5, 0.7].map((frac) => line("ben-chiro", f(frac), 7000, "Subsequent consultation", { itemCode: "1505" })),
      ],
      status: "paid",
    },
    {
      member: "m-a",
      provider: "Bondi Physio Co",
      providerType: "Physiotherapist",
      lines: [line("ben-physio", f(0.92), 8500, "Subsequent consultation", { itemCode: "505" })],
      status: "submitted",
    },
    {
      member: "m-a",
      provider: "Mindful Psychology",
      providerType: "Psychologist",
      lines: [0.25, 0.4, 0.55].map((frac) => line("ben-psychology", f(frac), 22000, "Psychology session")),
      status: "paid",
    },
    {
      member: "m-a",
      provider: "Bayside Dental",
      providerType: "Dentist",
      lines: [
        line("ben-general-dental", f(0.4), 6500, "Periodic oral examination", { itemCode: "012" }),
        line("ben-general-dental", f(0.4), 12000, "Removal of calculus", { itemCode: "114" }),
        line("ben-general-dental", f(0.4), 9000, "Intraoral periapical radiographs", { itemCode: "022", quantity: 2 }),
      ],
      status: "paid",
      receipt: true,
    },
    {
      member: "m-b",
      provider: "Specsavvy Optical",
      providerType: "Optometrist",
      lines: [line("ben-optical", f(0.5), 32000, "Prescription glasses")],
      status: "paid",
    },
    {
      member: "m-b",
      provider: "Bayside Dental",
      providerType: "Dentist",
      lines: [line("ben-major-dental", f(0.65), 180000, "Crown, indirect ceramic")],
      status: "partially_paid",
      paidShare: 0.9,
      note: "Paid at the fund's agreed rate",
    },
    {
      member: "m-b",
      provider: "Bayside Dental",
      providerType: "Dentist",
      lines: [
        line("ben-general-dental", { daysAgo: 712 }, 6500, "Periodic oral examination", { itemCode: "012" }),
        line("ben-general-dental", { daysAgo: 712 }, 12000, "Removal of calculus", { itemCode: "114" }),
      ],
      status: "draft",
    },
    {
      member: "m-b",
      provider: "City Wellness Massage",
      providerType: "Massage therapist",
      lines: [line("ben-remedial-massage", f(0.55), 9000, "Relaxation massage", { itemCode: "205" })],
      status: "rejected",
      note: "Provider isn't recognised by the fund",
    },
    {
      member: "m-b",
      provider: "Mindful Psychology",
      providerType: "Psychologist",
      lines: [line("ben-psychology", f(0.97), 22000, "Psychology session")],
      status: "draft",
      byAgent: true,
    },
  ];
}

const DEMO_NAMES: Record<Region, Record<string, string>> = {
  CA: { "m-a": "Jordan Tremblay", "m-b": "Sam Tremblay", "m-c": "Riley Tremblay" },
  AU: { "m-a": "Mia Nguyen", "m-b": "Tom Nguyen" },
};

const DEMO_POLICY: Record<Region, string> = { CA: "NW-448120-07", AU: "WHF 90412733" };

export interface DemoData {
  plan: PlanRecord;
  claims: Claim[];
  receipts: ReceiptRecord[];
  aliases: AliasRecord[];
}

function at(date: IsoDate, hour: number, today: IsoDate, now: Date): Date {
  if (date >= today) return now;
  return new Date(`${date}T${String(hour).padStart(2, "0")}:15:00`);
}

export function buildDemo(region: Region, today: IsoDate = todayIso(), now: Date = new Date()): DemoData {
  const source = DEMO_PLANS[region];
  const plan: Plan = { ...source, document: source.document ? { ...source.document, isDemo: true } : { name: "Sample plan", pageCount: 0, extractedAt: null, isDemo: true } };
  const index = indexPlan(plan);
  const year = currentBenefitPeriod(plan, today);
  const yearDays = Math.max(0, daysBetween(year.start, today));

  const resolve = (when: When, benefitId: string): IsoDate => {
    let date: IsoDate;
    if ("windowOf" in when) {
      const benefit = index.benefits.get(when.windowOf)!;
      const window = periodWindow(benefit.limits[0].period, today, plan) ?? year;
      date = addDays(window.start, Math.round(Math.max(0, daysBetween(window.start, today)) * when.frac));
    } else if ("daysAgo" in when) {
      date = addDays(today, -when.daysAgo);
    } else {
      date = addDays(year.start, Math.round(yearDays * when.frac));
    }
    const benefit = index.benefits.get(benefitId)!;
    const covered = waitingPeriodEnds(benefit, plan) ?? plan.effectiveDate;
    if (covered && date < covered) date = covered;
    return minDate(date, today);
  };

  const specs = (region === "CA" ? caClaims(today, plan) : auClaims()).map((spec) => ({
    spec,
    lines: spec.lines.map((l) => ({ ...l, date: resolve(l.when, l.benefitId) })),
  }));
  specs.sort((a, b) => a.lines[0].date.localeCompare(b.lines[0].date));

  const claims: Claim[] = [];
  const receipts: ReceiptRecord[] = [];
  specs.forEach(({ spec, lines }, n) => {
    const serviceDate = lines[0].date;
    const actor = spec.byAgent ? DEMO_AGENT : USER_ACTOR;
    const draftLines = lines.map((l) => ({
      serviceDate: l.date,
      benefitId: l.benefitId,
      itemCode: l.itemCode ?? null,
      description: l.description,
      quantity: l.quantity ?? 1,
      chargedCents: l.charged,
      otherPlanPaidCents: 0,
    }));
    let claim = createDraftClaim(plan, { patientMemberId: spec.member, provider: spec.provider, lines: draftLines }, actor, at(serviceDate, 18, today, now));
    claim = { ...claim, id: `demo-${region.toLowerCase()}-${String(n + 1).padStart(2, "0")}` };
    if (spec.status !== "draft") {
      claim = updateClaim(plan, claim, { status: "submitted" }, USER_ACTOR, at(addDays(serviceDate, 1), 9, today, now));
    }
    if (spec.status === "paid" || spec.status === "partially_paid" || spec.status === "rejected") {
      const decidedOn = minDate(addDays(serviceDate, 8), today);
      const estimate = estimateClaim(plan, claims, claim, { today, includeSubmitted: false }).planPaysCents;
      const paidCents = spec.status === "rejected" ? 0 : Math.round(estimate * (spec.paidShare ?? 1));
      claim = updateClaim(
        plan,
        claim,
        { status: spec.status, outcome: { paidCents, decidedOn, note: spec.note ?? null } },
        USER_ACTOR,
        at(decidedOn, 14, today, now),
      );
    }
    if (spec.receipt) {
      const receiptId = `${claim.id}-receipt`;
      receipts.push({
        id: receiptId,
        planId: plan.id,
        claimId: claim.id,
        receipt: {
          providerName: spec.provider,
          providerType: spec.providerType,
          providerRegistrationNo: null,
          serviceLines: lines.map((l) => ({
            serviceDate: l.date,
            description: l.description,
            itemCode: l.itemCode ?? null,
            quantity: l.quantity ?? 1,
            amountCents: l.charged,
          })),
          totalCents: lines.reduce((s, l) => s + l.charged, 0),
          insurerPaidCents: null,
          currency: plan.currency,
        },
        fieldConfidence: {},
        issues: [],
        source: "demo",
        createdAt: claim.createdAt,
      });
      claim = { ...claim, attachments: [{ receiptId, kind: "receipt" }] };
    }
    claims.push(claim);
  });

  const created = now.toISOString();
  const aliases: AliasRecord[] = [
    ...plan.members.map<AliasRecord>((m) => ({
      id: `demo-${region.toLowerCase()}-${m.id}`,
      kind: "member",
      token: m.alias,
      values: [DEMO_NAMES[region][m.id]].filter(Boolean),
      relationship: m.relationship,
      createdAt: created,
    })),
    {
      id: `demo-${region.toLowerCase()}-policy`,
      kind: "policy",
      token: plan.identifiers[0] ?? "[POLICY_1]",
      values: [DEMO_POLICY[region]],
      createdAt: created,
    },
  ];

  return {
    plan: { id: plan.id, plan, isDemo: true, createdAt: created, updatedAt: created },
    claims,
    receipts,
    aliases,
  };
}

export async function clearDemo(region: Region, database: BenefuraDB = defaultDb): Promise<void> {
  const planId = DEMO_PLAN_IDS[region];
  await database.transaction("rw", [database.plans, database.claims, database.receipts, database.aliases, database.settings], async () => {
    await database.claims.where("planId").equals(planId).delete();
    await database.receipts.where("planId").equals(planId).delete();
    await database.aliases.filter((a) => a.id.startsWith(`demo-${region.toLowerCase()}-`)).delete();
    await database.plans.delete(planId);
    const active = await database.settings.get(SETTING_KEYS.activePlanId);
    if (active?.value === planId) await database.settings.delete(SETTING_KEYS.activePlanId);
  });
}

export async function loadDemo(
  region: Region,
  opts: { today?: IsoDate; now?: Date; database?: BenefuraDB } = {},
): Promise<string> {
  const database = opts.database ?? defaultDb;
  const data = buildDemo(region, opts.today ?? todayIso(), opts.now ?? new Date());
  await clearDemo(region, database);
  await database.transaction("rw", [database.plans, database.claims, database.receipts, database.aliases, database.settings], async () => {
    await database.plans.put(data.plan);
    await database.claims.bulkPut(data.claims);
    await database.receipts.bulkPut(data.receipts);
    await database.aliases.bulkPut(data.aliases);
    await database.settings.put({ key: SETTING_KEYS.activePlanId, value: data.plan.id });
  });
  return data.plan.id;
}
