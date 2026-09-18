import { describe, expect, it } from "vitest";

import { claimTotals, ClaimError, createDraftClaim, groupClaimsByStatus, updateClaim, USER_ACTOR, type ClaimActor } from "../claims";
import { AU_DEMO_PLAN as AU, CA_DEMO_PLAN as CA } from "../fixtures";

const now = new Date("2026-09-16T15:00:00Z");
const later = new Date("2026-09-20T09:30:00Z");
const agent: ClaimActor = { actor: "agent", agentName: "benefura-plan-claims", agentVersion: "4", traceId: "0af7651916cd43dd8448eb211c80319c" };

const line = { serviceDate: "2026-09-10", benefitId: "ben-massage", itemCode: null, description: "RMT 60 min", quantity: 1, chargedCents: 12000, otherPlanPaidCents: 0 };

function draft() {
  return createDraftClaim(CA, { patientMemberId: "m-a", provider: "  Harbour Massage  ", lines: [line] }, USER_ACTOR, now);
}

describe("createDraftClaim", () => {
  it("creates a draft with ids, deadline and history", () => {
    const c = draft();
    expect(c.id).toMatch(/^clm-/);
    expect(c.lines[0].id).toMatch(/^line-/);
    expect(c).toMatchObject({ planId: CA.id, status: "draft", provider: "Harbour Massage", deadline: "2027-03-31", outcome: null });
    expect(c.history).toEqual([
      { status: "draft", at: now.toISOString(), actor: "user", agentName: null, agentVersion: null, traceId: null, note: "Draft created" },
    ]);
  });

  it("uses the earliest line deadline", () => {
    const c = createDraftClaim(
      AU,
      { patientMemberId: "m-a", lines: [{ ...line, benefitId: "ben-ambulance", serviceDate: "2026-06-01" }, { ...line, benefitId: "ben-ambulance", serviceDate: "2025-01-01" }] },
      USER_ACTOR,
      now,
    );
    expect(c.deadline).toBe("2027-01-01");
  });

  it("records agent provenance", () => {
    const c = createDraftClaim(CA, { patientMemberId: "m-b", lines: [line] }, agent, now);
    expect(c.history[0]).toMatchObject({ actor: "agent", agentName: "benefura-plan-claims", agentVersion: "4", traceId: agent.traceId });
  });

  it("validates members, benefits and amounts", () => {
    expect(() => createDraftClaim(CA, { patientMemberId: "m-z", lines: [line] }, USER_ACTOR, now)).toThrow(ClaimError);
    expect(() => createDraftClaim(CA, { patientMemberId: "m-a", lines: [{ ...line, benefitId: "nope" }] }, USER_ACTOR, now)).toThrow(/benefit/);
    expect(() => createDraftClaim(CA, { patientMemberId: "m-a", lines: [{ ...line, chargedCents: 10.5 }] }, USER_ACTOR, now)).toThrow(/charged/);
    expect(() => createDraftClaim(CA, { patientMemberId: "m-a", lines: [{ ...line, otherPlanPaidCents: 20000 }] }, USER_ACTOR, now)).toThrow(/other plan/);
    expect(() => createDraftClaim(CA, { patientMemberId: "m-a", lines: [{ ...line, serviceDate: "2026-02-30" }] }, USER_ACTOR, now)).toThrow(/date/);
  });
});

describe("updateClaim", () => {
  it("moves draft to submitted to paid with history", () => {
    const submitted = updateClaim(CA, draft(), { status: "submitted" }, USER_ACTOR, now);
    const paid = updateClaim(CA, submitted, { status: "paid", outcome: { paidCents: 8000, decidedOn: "2026-09-19", note: null } }, USER_ACTOR, later);
    expect(paid.status).toBe("paid");
    expect(paid.outcome).toEqual({ paidCents: 8000, decidedOn: "2026-09-19", note: null });
    expect(paid.history.map((h) => h.status)).toEqual(["draft", "submitted", "paid"]);
    expect(paid.updatedAt).toBe(later.toISOString());
    expect(claimTotals(paid)).toEqual({ chargedCents: 12000, paidCents: 8000 });
  });

  it("rejects invalid transitions", () => {
    const d = draft();
    expect(() => updateClaim(CA, d, { status: "paid", outcome: { paidCents: 1, decidedOn: null, note: null } }, USER_ACTOR, now)).toThrow(ClaimError);
    const paid = updateClaim(CA, updateClaim(CA, d, { status: "submitted" }, USER_ACTOR, now), { status: "paid", outcome: { paidCents: 8000, decidedOn: null, note: null } }, USER_ACTOR, now);
    expect(() => updateClaim(CA, paid, { status: "draft" }, USER_ACTOR, now)).toThrow(/can't move/);
    expect(() => updateClaim(CA, paid, { status: "submitted" }, USER_ACTOR, now)).toThrow(/can't move/);
  });

  it("allows back-edits to draft only from submitted and clears the outcome", () => {
    const submitted = updateClaim(CA, draft(), { status: "submitted" }, USER_ACTOR, now);
    expect(() => updateClaim(CA, submitted, { provider: "Other" }, USER_ACTOR, now)).toThrow(/back to draft/);
    const back = updateClaim(CA, submitted, { status: "draft", provider: "Other" }, USER_ACTOR, later);
    expect(back).toMatchObject({ status: "draft", provider: "Other", outcome: null });
    expect(back.history.map((h) => h.status)).toEqual(["draft", "submitted", "draft"]);
  });

  it("requires a paid amount and caps it at the charge", () => {
    const submitted = updateClaim(CA, draft(), { status: "submitted" }, USER_ACTOR, now);
    expect(() => updateClaim(CA, submitted, { status: "partially_paid" }, USER_ACTOR, now)).toThrow(/amount/);
    expect(() => updateClaim(CA, submitted, { status: "paid", outcome: { paidCents: 99999, decidedOn: null, note: null } }, USER_ACTOR, now)).toThrow(/more than/);
    const rejected = updateClaim(CA, submitted, { status: "rejected", outcome: { paidCents: 500, decidedOn: null, note: "No referral" } }, USER_ACTOR, now);
    expect(rejected.outcome).toEqual({ paidCents: 0, decidedOn: null, note: "No referral" });
    expect(updateClaim(CA, rejected, { status: "partially_paid", outcome: { paidCents: 4000, decidedOn: null, note: null } }, USER_ACTOR, now).status).toBe("partially_paid");
  });

  it("recomputes the deadline when lines change and logs agent edits", () => {
    const d = draft();
    const edited = updateClaim(CA, d, { lines: [{ ...d.lines[0], serviceDate: "2027-02-01" }] }, agent, later);
    expect(edited.deadline).toBe("2028-03-30");
    expect(edited.lines[0].id).toBe(d.lines[0].id);
    expect(edited.history.at(-1)).toMatchObject({ status: "draft", actor: "agent", traceId: agent.traceId, note: "Updated by the assistant" });
    expect(updateClaim(CA, d, { provider: "X" }, USER_ACTOR, later).history).toHaveLength(1);
  });

  it("groups claims by status with totals", () => {
    const a = draft();
    const b = updateClaim(CA, updateClaim(CA, draft(), { status: "submitted" }, USER_ACTOR, now), { status: "paid", outcome: { paidCents: 8000, decidedOn: null, note: null } }, USER_ACTOR, now);
    const groups = groupClaimsByStatus([b, a, draft()]);
    expect(groups.map((g) => [g.status, g.claims.length, g.chargedCents, g.paidCents])).toEqual([
      ["draft", 2, 24000, 0],
      ["paid", 1, 12000, 8000],
    ]);
  });
});
