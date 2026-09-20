import { describe, expect, it } from "vitest";

import { AU_DEMO_PLAN, CA_DEMO_PLAN } from "@/domain/fixtures";

import { emptyLine, lineFromReceipt, lineProblems, parseMoney, toClaimLine } from "./lineDraft";

describe("parseMoney", () => {
  it("reads amounts people type", () => {
    expect(parseMoney("120")).toBe(12000);
    expect(parseMoney("120.5")).toBe(12050);
    expect(parseMoney("$1,234.56")).toBe(123456);
    expect(parseMoney(" 0.07 ")).toBe(7);
    expect(parseMoney("")).toBeNull();
    expect(parseMoney("12.345")).toBeNull();
    expect(parseMoney("-5")).toBeNull();
    expect(parseMoney("abc")).toBeNull();
  });
});

describe("receipt lines", () => {
  it("guesses the benefit from the item number, then the description, then the provider type", () => {
    const today = "2026-09-16";
    const physio = lineFromReceipt(AU_DEMO_PLAN, { serviceDate: "2026-09-02", description: "Consult", itemCode: "505", quantity: 1, amountCents: 8500 }, null, today);
    expect(physio).toMatchObject({ benefitId: "ben-physio", serviceDate: "2026-09-02", charged: "85.00", itemCode: "505" });

    const rmt = lineFromReceipt(CA_DEMO_PLAN, { serviceDate: null, description: "RMT 60 minutes", itemCode: null, quantity: null, amountCents: 12000 }, null, today);
    expect(rmt).toMatchObject({ benefitId: "ben-massage", serviceDate: today, quantity: "1" });

    const byType = lineFromReceipt(CA_DEMO_PLAN, { serviceDate: "2026-13-01", description: null, itemCode: null, quantity: 2, amountCents: null }, "Chiropractor", today);
    expect(byType).toMatchObject({ benefitId: "ben-chiro", serviceDate: today, quantity: "2", charged: "" });
    expect(toClaimLine(byType)).toBeNull();
    expect(lineProblems(byType)).toEqual(["charged"]);
  });

  it("converts a complete draft to cents", () => {
    const line = { ...emptyLine("2026-09-16", "ben-massage"), charged: "120", otherPaid: "50.25" };
    expect(toClaimLine(line)).toMatchObject({ chargedCents: 12000, otherPlanPaidCents: 5025, quantity: 1, itemCode: null });
    expect(lineProblems({ ...line, otherPaid: "130" })).toEqual(["other"]);
  });
});
