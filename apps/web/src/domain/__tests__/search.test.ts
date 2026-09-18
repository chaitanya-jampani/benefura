import { describe, expect, it } from "vitest";

import { AU_DEMO_PLAN as AU, CA_DEMO_PLAN as CA } from "../fixtures";
import { findBenefits, searchPlanDocument } from "../search";

const top = (plan: typeof CA, q: string) => findBenefits(plan, q)[0]?.benefitId;

describe("findBenefits", () => {
  it("finds benefits by keyword, abbreviation and name", () => {
    expect(top(CA, "RMT")).toBe("ben-massage");
    expect(top(CA, "glasses")).toBe("ben-eyewear");
    expect(top(CA, "physio")).toBe("ben-physio");
    expect(top(CA, "psychologist")).toBe("ben-mental-health");
    expect(top(CA, "crowns")).toBe("ben-dental-major");
    expect(top(AU, "massage")).toBe("ben-remedial-massage");
  });

  it("tolerates typos and partial words", () => {
    expect(top(CA, "massge")).toBe("ben-massage");
    expect(top(CA, "ortho")).toBe("ben-orthotics");
    expect(top(AU, "chiropract")).toBe("ben-chiro");
  });

  it("matches item numbers exactly, not fuzzily", () => {
    expect(top(AU, "item 505")).toBe("ben-physio");
    expect(top(AU, "1505")).toBe("ben-chiro");
    expect(top(AU, "205")).toBe("ben-remedial-massage");
    expect(findBenefits(AU, "505").map((h) => h.benefitId)).not.toContain("ben-chiro");
    expect(top(CA, "01202")).toBe("ben-dental-recall");
  });

  it("returns category names and respects the limit", () => {
    const hits = findBenefits(CA, "dental", 2);
    expect(hits).toHaveLength(2);
    expect(hits[0].categoryName).toBe("Dental care");
    expect(findBenefits(CA, "   ")).toEqual([]);
  });
});

describe("searchPlanDocument", () => {
  it("returns the page and quote for rules", () => {
    const deductible = searchPlanDocument(CA, "deductible");
    expect(deductible[0].page).toBe(8);
    expect(deductible.some((h) => h.quote.includes("$25 per family"))).toBe(true);
    const deadline = searchPlanDocument(CA, "claims received 90 days");
    expect(deadline[0]).toMatchObject({ page: 13, benefitId: null });
  });

  it("covers notes, requirements and AU clinical categories", () => {
    expect(searchPlanDocument(CA, "prescription DIN")[0].benefitId).toBe("ben-drugs");
    expect(searchPlanDocument(AU, "pregnancy")[0]).toMatchObject({ page: 6, quote: "Pregnancy and birth: Not covered" });
    expect(searchPlanDocument(AU, "HICAPS")[0].page).toBe(11);
  });
});
