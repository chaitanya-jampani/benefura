import { describe, expect, it } from "vitest";

import { AliasBook } from "./apply";
import { candidatesForPage } from "./detectors";
import { buildTextStream, rangeRects } from "./detectors/text-stream";
import { closePdf, extractPageText, needsOcr, openPdf } from "./pdf-text";
import { PDFJS_NODE_ASSETS } from "./test-utils/node-canvas";
import { FAKE, makeBookletPdf } from "./test-utils/pdf";

describe("pdf text extraction", () => {
  it("reads runs with page positions from a pdf-lib document", async () => {
    const pdf = await openPdf(await makeBookletPdf(), { assetBase: PDFJS_NODE_ASSETS });
    const text = await extractPageText(await pdf.getPage(1), 0);
    expect(text.widthPt).toBe(612);
    expect(text.heightPt).toBe(792);
    expect(text.source).toBe("pdf");
    expect(needsOcr(text)).toBe(false);

    const member = text.runs.find((r) => r.str.startsWith("Plan member"))!;
    expect(member.ox).toBeCloseTo(72, 1);
    // pdf-lib's y=680 baseline is 792-680=112pt from the top.
    expect(member.oy).toBeCloseTo(112, 1);
    expect(member.dx).toBe(1);

    const stream = buildTextStream(text);
    expect(stream.text).toContain(`Social insurance number: ${FAKE.sin}`);
    const start = stream.text.indexOf(FAKE.sin);
    const [rect] = rangeRects(stream, start, start + FAKE.sin.length);
    expect(rect.y * 792).toBeGreaterThan(120);
    expect(rect.y * 792).toBeLessThan(135);
    await closePdf(pdf);
  });

  it("detects the fake PII, including the value split across two fonts", async () => {
    const pdf = await openPdf(await makeBookletPdf(), { assetBase: PDFJS_NODE_ASSETS });
    const text = await extractPageText(await pdf.getPage(1), 0);
    const candidates = candidatesForPage(
      text,
      { region: "CA", known: [{ token: "[MEMBER_A]", kind: "member", value: FAKE.name, isName: true }] },
      new AliasBook(),
    );
    const values = candidates.map((c) => c.value);
    expect(values).toEqual(expect.arrayContaining([FAKE.name, FAKE.sin, FAKE.phone, FAKE.certificate]));
    expect(candidates.find((c) => c.value === FAKE.sin)?.box.detector).toBe("ca.sin");
    await closePdf(pdf);
  });

  it("maps text on a rotated page into the rotated page frame", async () => {
    const pdf = await openPdf(await makeBookletPdf({ pages: 2, rotateSecond: true }), { assetBase: PDFJS_NODE_ASSETS });
    const text = await extractPageText(await pdf.getPage(2), 1);
    expect(text.widthPt).toBe(792);
    expect(text.heightPt).toBe(612);
    const stream = buildTextStream(text);
    const start = stream.text.indexOf(FAKE.name);
    expect(start).toBeGreaterThanOrEqual(0);
    const [rect] = rangeRects(stream, start, start + FAKE.name.length);
    // Rotated 90° clockwise, text runs top to bottom: the box is taller than it is wide.
    expect(rect.h * 612).toBeGreaterThan(rect.w * 792);
    expect(rect.x).toBeGreaterThanOrEqual(0);
    expect(rect.x + rect.w).toBeLessThanOrEqual(1);
    await closePdf(pdf);
  });

  it("flags pages without a text layer for OCR", async () => {
    const pdf = await openPdf(await makeBookletPdf({ pages: 2, imageOnlyPage: true }), { assetBase: PDFJS_NODE_ASSETS });
    const text = await extractPageText(await pdf.getPage(2), 1);
    expect(text.source).toBe("none");
    expect(needsOcr(text)).toBe(true);
    await closePdf(pdf);
  });
});
