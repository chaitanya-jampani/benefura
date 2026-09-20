import { describe, expect, it } from "vitest";

import { AliasBook } from "../apply";
import { makePage, textWidth } from "../test-utils/page";
import type { BoxCandidate, KnownValue } from "../types";
import { candidatesForPage, detectInStream, findValueOnPage } from "./index";
import { findKnownValues } from "./known-values";
import { detectLabels, findLabels } from "./labels";
import { buildTextStream, rangeRects } from "./text-stream";

const JORDAN: KnownValue = { token: "[MEMBER_A]", kind: "member", value: "Jordan Testcase", isName: true };

function detect(lines: Parameters<typeof makePage>[0], region: "CA" | "AU" = "CA", known: KnownValue[] = []) {
  const page = makePage(lines);
  return candidatesForPage(page, { region, known }, new AliasBook());
}

const byDetector = (cs: BoxCandidate[], detector: string) => cs.filter((c) => c.box.detector === detector);

describe("text stream", () => {
  it("joins split runs without a separator and keeps word gaps", () => {
    const page = makePage([[{ str: "SIN 046 45" }, { str: "4 286", gap: 0 }], [{ str: "Name" }, { str: "Jordan", gap: 200 }]]);
    const stream = buildTextStream(page);
    expect(stream.text).toBe("SIN 046 454 286\nName\tJordan");
  });

  it("orders runs by line and position, not by drawing order", () => {
    const page = makePage([[{ str: "left" }, { str: "right" }]]);
    page.runs.reverse();
    expect(buildTextStream(page).text).toBe("left right");
  });

  it("maps a range across split runs to one rectangle, and across lines to one per line", () => {
    const page = makePage([[{ str: "Jor" }, { str: "dan Test", gap: 0 }, { str: "case", gap: 0 }], "second line"]);
    const stream = buildTextStream(page);
    const start = stream.text.indexOf("Jordan");
    const rects = rangeRects(stream, start, start + "Jordan Testcase".length);
    expect(rects).toHaveLength(1);
    expect(rects[0].x * 612).toBeCloseTo(72, 0);
    expect(rects[0].w * 612).toBeCloseTo(textWidth("Jordan Testcase"), 0);

    const across = rangeRects(stream, stream.text.indexOf("case"), stream.text.indexOf("second") + 6);
    expect(across).toHaveLength(2);
  });

  it("places characters inside a run by glyph width", () => {
    const page = makePage(["Member: Jordan"]);
    const stream = buildTextStream(page);
    const start = stream.text.indexOf("Jordan");
    const [rect] = rangeRects(stream, start, start + 6);
    expect(rect.x * 612).toBeCloseTo(72 + textWidth("Member: "), 1);
    expect(rect.w * 612).toBeCloseTo(textWidth("Jordan"), 1);
  });
});

describe("known values", () => {
  it("matches across split text runs", () => {
    const page = makePage([[{ str: "Plan member Jor" }, { str: "dan Test", gap: 0 }, { str: "case", gap: 0 }]]);
    const hits = findKnownValues(0, buildTextStream(page), [JORDAN]);
    expect(hits.some((h) => h.detector === "known" && h.value === "Jordan Testcase")).toBe(true);
  });

  it("ignores case, spacing and punctuation", () => {
    const page = makePage(["Call 416.555.0199 or JORDAN   TESTCASE", "Email: Jordan.Testcase@Example.com"]);
    const stream = buildTextStream(page);
    const known: KnownValue[] = [
      JORDAN,
      { token: "[PHONE_A]", kind: "phone", value: "(416) 555-0199" },
      { token: "[EMAIL_A]", kind: "email", value: "jordan.testcase@example.com" },
    ];
    const values = findKnownValues(0, stream, known).filter((h) => h.detector === "known").map((h) => h.token);
    expect(values).toEqual(expect.arrayContaining(["[MEMBER_A]", "[PHONE_A]", "[EMAIL_A]"]));
  });

  it("respects word boundaries", () => {
    const page = makePage(["Annual maximum for Ann"]);
    const hits = findKnownValues(0, buildTextStream(page), [{ token: "[MEMBER_B]", kind: "member", value: "Ann" }]);
    expect(hits).toHaveLength(1);
    expect(hits[0].value).toBe("Ann");
  });

  it("finds reversed names and name parts", () => {
    const page = makePage(["TESTCASE, Jordan", "Dear Jordan,"]);
    const hits = findKnownValues(0, buildTextStream(page), [JORDAN]);
    expect(hits.some((h) => h.value === "TESTCASE, Jordan" && h.detector === "known")).toBe(true);
    expect(hits.some((h) => h.value === "Jordan" && h.detector === "known.part")).toBe(true);
  });

  it("matches street abbreviations and phone numbers with a country code", () => {
    const page = makePage(["12 Maple St, Toronto", "+1 416 555 0199"]);
    const stream = buildTextStream(page);
    const hits = findKnownValues(0, stream, [
      { token: "[ADDRESS_A]", kind: "address", value: "12 Maple Street, Toronto" },
      { token: "[PHONE_A]", kind: "phone", value: "416-555-0199" },
    ]);
    expect(hits.some((h) => h.token === "[ADDRESS_A]" && h.value === "12 Maple St, Toronto")).toBe(true);
    expect(hits.some((h) => h.token === "[PHONE_A]")).toBe(true);
  });

  it("tolerates OCR letter/digit confusion for long numbers on scanned pages", () => {
    const page = makePage(["Certificate NW-447I-OO93"], { source: "ocr" });
    const known = [{ token: "[CERT_1]", kind: "policy" as const, value: "NW-4471-0093" }];
    expect(findKnownValues(0, buildTextStream(page), known, "ocr")).toHaveLength(1);
    expect(findKnownValues(0, buildTextStream(page), known, "pdf")).toHaveLength(0);
  });
});

describe("label anchors", () => {
  it("captures values after labels and stops at the next field", () => {
    const page = makePage(["Plan member: Jordan Testcase Policy number: 88213", "Certificate no. NW-4471-0093", "Date of birth: 1985-06-15"]);
    const stream = buildTextStream(page);
    const hits = detectLabels(0, stream, findLabels(stream));
    const byId = Object.fromEntries(hits.map((h) => [h.detector, h]));
    expect(byId["label.member_name"].value).toBe("Jordan Testcase");
    expect(byId["label.policy"].value).toBe("88213");
    expect(byId["label.certificate"].value).toBe("NW-4471-0093");
    expect(byId["label.dob"].value).toBe("1985-06-15");
    expect(byId["label.dob"].family).toBeNull();
  });

  it("reads a value on the next line when the label ends its line", () => {
    const page = makePage(["Member name", "Jordan Testcase"]);
    const stream = buildTextStream(page);
    const hits = detectLabels(0, stream, findLabels(stream));
    expect(hits.map((h) => h.value)).toContain("Jordan Testcase");
  });

  it("requires a colon or column gap for generic words", () => {
    const stream = buildTextStream(makePage(["Benefit name: Massage therapy", "The patient Receives care", "Name: Riley Testcase"]));
    const values = detectLabels(0, stream, findLabels(stream)).map((h) => h.value);
    expect(values).toEqual(["Riley Testcase"]);
  });
});

describe("Canadian detectors", () => {
  it("finds a Luhn-valid SIN without a label but ignores invalid ones unless labelled", () => {
    expect(byDetector(detect(["Reference 046 454 286"]), "ca.sin")).toHaveLength(1);
    expect(byDetector(detect(["Reference 046 454 287"]), "ca.sin")).toHaveLength(0);
    const labelled = detect(["SIN: 046 454 287"]);
    expect(labelled).toHaveLength(1);
    expect(labelled[0].box.token).toBe("[ID_1]");
  });

  it("scores checksum + label above a bare pattern", () => {
    const [labelled] = detect(["Social insurance number: 046 454 286"]);
    const [bare] = detect(["Reference 046 454 286"]);
    expect(labelled.box.confidence).toBeGreaterThan(bare.box.confidence);
  });

  it("detects Ontario, BC and Québec health numbers", () => {
    const cs = detect(["Health card 9876 543 217 AB", "PHN 9698 658 215", "RAMQ TESJ 8506 1512"]);
    const detectors = cs.map((c) => c.box.detector);
    expect(detectors).toEqual(expect.arrayContaining(["ca.on_health", "ca.bc_phn", "ca.ramq"]));
  });

  it("boxes postal codes and phones, leaving toll-free lines off by default", () => {
    const cs = detect(["Toronto ON M5V 2T6", "Home (416) 555-0199", "Claims 1-800-555-0100"]);
    expect(byDetector(cs, "ca.postal")[0].box.enabled).toBe(true);
    const phones = byDetector(cs, "ca.phone");
    expect(phones.map((p) => p.box.enabled)).toEqual([true, false]);
  });
});

describe("Australian detectors", () => {
  it("finds TFN, Medicare and IHI by checksum", () => {
    const cs = detect(["TFN 123 456 782", "Medicare 2951 23457 1", "IHI 8003 6012 3456 7894"], "AU");
    expect(cs.map((c) => c.box.detector)).toEqual(expect.arrayContaining(["au.tfn", "au.medicare", "au.ihi"]));
  });

  it("keeps ABNs visible by default and needs a label for BSBs", () => {
    const cs = detect(["Wattle Health ABN 51 824 753 556", "BSB: 062-000", "Code 062-000"], "AU");
    expect(byDetector(cs, "au.abn")[0].box.enabled).toBe(false);
    expect(byDetector(cs, "au.bsb")).toHaveLength(1);
  });

  it("detects postcodes with a state and mobile numbers", () => {
    const cs = detect(["Sydney NSW 2000", "Mobile: 0412 345 678", "Call 13 23 45"], "AU");
    expect(byDetector(cs, "au.postcode")).toHaveLength(1);
    const phones = cs.filter((c) => c.box.kind === "phone");
    expect(phones.find((p) => p.value.includes("0412"))?.box.enabled).toBe(true);
    expect(phones.find((p) => p.value.includes("13 23 45"))?.box.enabled).toBe(false);
  });
});

describe("candidates", () => {
  it("leaves shared inboxes visible but hides personal email", () => {
    const cs = detect(["claims@northwind.example", "jordan.t@example.com"]);
    expect(cs.map((c) => c.box.enabled)).toEqual([false, true]);
  });

  it("merges overlapping hits into one box with the strongest token", () => {
    const cs = detect(["Plan member: Jordan Testcase"], "CA", [JORDAN]);
    expect(cs).toHaveLength(1);
    expect(cs[0].box.token).toBe("[MEMBER_A]");
    expect(cs[0].box.enabled).toBe(true);
  });

  it("keeps an email that contains the member's name labelled as an email", () => {
    const cs = detect(["Email: jordan.testcase@example.com"], "CA", [JORDAN]);
    expect(cs).toHaveLength(1);
    expect(cs[0].box.token).toBe("[EMAIL_A]");
    expect(cs[0].value).toBe("jordan.testcase@example.com");
  });

  it("gives a repeated value the same token", () => {
    const book = new AliasBook();
    const a = candidatesForPage(makePage(["SIN: 046 454 286"]), { region: "CA", known: [] }, book);
    const b = candidatesForPage(makePage(["Your SIN 046-454-286 and 130 692 544"], { pageIndex: 1 }), { region: "CA", known: [] }, book);
    expect(a[0].box.token).toBe("[ID_1]");
    expect(b.map((c) => c.box.token)).toEqual(["[ID_1]", "[ID_2]"]);
  });

  it("finds all occurrences of a value added during review", () => {
    const page = makePage(["Ref 7Q-88213-X", "see 7Q 88213 X again"]);
    const hits = findValueOnPage(page, "7Q-88213-X", null, "custom");
    expect(hits).toHaveLength(2);
    expect(hits.every((h) => h.box.source === "manual" && h.box.token === null)).toBe(true);
  });

  it("does not flag plain benefit text", () => {
    const stream = buildTextStream(makePage(["Massage therapy: 80% up to $80 per visit, to a maximum of $500 per person per benefit year"]));
    expect(detectInStream(0, stream, { region: "CA", known: [] })).toEqual([]);
  });
});
