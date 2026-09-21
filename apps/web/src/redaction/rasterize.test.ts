import { createCanvas, loadImage } from "@napi-rs/canvas";
import { describe, expect, it } from "vitest";

import type { RedactionBox } from "@/db/dexie";

import { AliasBook } from "./apply";
import { candidatesForPage } from "./detectors";
import { closePdf, extractPageText, openPdf, pdfPageRenderer } from "./pdf-text";
import { boxToPixels, layoutLabel, pagePixelSize, rasterizePage } from "./rasterize";
import { nodeCanvasFactory, PDFJS_NODE_ASSETS, sampleGrid } from "./test-utils/node-canvas";
import { FAKE, makeBookletPdf } from "./test-utils/pdf";
import { ctx2d, type PageRenderer } from "./types";

const DPI = 150;

async function decodeLuma(jpeg: Uint8Array) {
  const img = await loadImage(Buffer.from(jpeg));
  const canvas = createCanvas(img.width, img.height);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(img, 0, 0);
  return { width: img.width, height: img.height, data: ctx.getImageData(0, 0, img.width, img.height).data };
}

const blankRenderer: PageRenderer = { widthPt: 612, heightPt: 792, render: async () => {} };

async function setup() {
  const pdf = await openPdf(await makeBookletPdf(), { assetBase: PDFJS_NODE_ASSETS });
  const page = await pdf.getPage(1);
  const text = await extractPageText(page, 0);
  const candidates = candidatesForPage(
    text,
    { region: "CA", known: [{ token: "[MEMBER_A]", kind: "member", value: FAKE.name, isName: true }] },
    new AliasBook(),
  );
  // Make the SIN a plain black box so both styles are exercised.
  const boxes: RedactionBox[] = candidates.map((c) => (c.value === FAKE.sin ? { ...c.box, token: null } : c.box));
  return { pdf, renderer: pdfPageRenderer(page), boxes };
}

describe("rasterizePage", () => {
  it("burns solid black boxes and label-only token boxes over the original text", async () => {
    const { pdf, renderer, boxes } = await setup();
    expect(boxes.some((b) => b.token === null)).toBe(true);
    expect(boxes.some((b) => b.token !== null)).toBe(true);

    const unredacted = await decodeLuma((await rasterizePage({ renderer, pageIndex: 0, boxes: [], dpi: DPI, factory: nodeCanvasFactory })).jpeg);
    const redacted = await rasterizePage({ renderer, pageIndex: 0, boxes, dpi: DPI, factory: nodeCanvasFactory });
    const reference = await decodeLuma((await rasterizePage({ renderer: blankRenderer, pageIndex: 0, boxes, dpi: DPI, factory: nodeCanvasFactory })).jpeg);
    const out = await decodeLuma(redacted.jpeg);

    expect(redacted.widthPx).toBe(Math.round(8.5 * DPI));
    expect(redacted.heightPx).toBe(11 * DPI);

    for (const box of boxes.filter((b) => b.enabled)) {
      const rect = boxToPixels(box, out.width, out.height);
      // The test is only meaningful if there was text under the box to begin with.
      expect(Math.min(...sampleGrid(unredacted.data, unredacted.width, rect, { inset: 1, step: 1 }))).toBeLessThan(100);

      const got = sampleGrid(out.data, out.width, rect, { inset: 2, step: 2 });
      if (box.token === null) {
        expect(Math.max(...got)).toBeLessThanOrEqual(40);
      } else {
        // Identical to the same label painted on a blank page: nothing of the original survives.
        const want = sampleGrid(reference.data, reference.width, rect, { inset: 2, step: 2 });
        const diffs = got.map((v, i) => Math.abs(v - want[i]));
        expect(Math.max(...diffs)).toBeLessThanOrEqual(60);
        expect(diffs.reduce((a, b) => a + b, 0) / diffs.length).toBeLessThan(6);
      }
    }
    await closePdf(pdf);
  });

  it("outputs grayscale", async () => {
    const { pdf, renderer, boxes } = await setup();
    const raster = await rasterizePage({ renderer, pageIndex: 0, boxes, dpi: 100, factory: nodeCanvasFactory });
    const img = await loadImage(Buffer.from(raster.jpeg));
    const canvas = createCanvas(img.width, img.height);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const { data } = ctx.getImageData(0, 0, img.width, img.height);
    let maxChroma = 0;
    for (let i = 0; i < data.length; i += 4 * 97) {
      maxChroma = Math.max(maxChroma, Math.abs(data[i] - data[i + 1]), Math.abs(data[i + 1] - data[i + 2]));
    }
    expect(maxChroma).toBeLessThanOrEqual(3);
    await closePdf(pdf);
  });

  it("ignores disabled boxes", async () => {
    const { pdf, renderer, boxes } = await setup();
    const disabled = boxes.map((b) => ({ ...b, enabled: false }));
    const a = await decodeLuma((await rasterizePage({ renderer, pageIndex: 0, boxes: disabled, dpi: 100, factory: nodeCanvasFactory })).jpeg);
    const b = await decodeLuma((await rasterizePage({ renderer, pageIndex: 0, boxes: [], dpi: 100, factory: nodeCanvasFactory })).jpeg);
    expect(Buffer.compare(Buffer.from(a.data), Buffer.from(b.data))).toBe(0);
    await closePdf(pdf);
  });
});

describe("label layout", () => {
  const ctx = ctx2d(nodeCanvasFactory.create(10, 10));

  it("fits a token inside a roomy box without widening", () => {
    const layout = layoutLabel(ctx, "[MEMBER_A]", { x: 100, y: 100, w: 600, h: 60 }, 300, { width: 2550, height: 3300 });
    expect(layout.rect).toEqual({ x: 100, y: 100, w: 600, h: 60 });
    expect(layout.scaleX).toBe(1);
    expect(layout.fontPx).toBeGreaterThanOrEqual(26);
  });

  it("condenses, then widens a box too narrow for its token", () => {
    const narrow = layoutLabel(ctx, "[MEMBER_A]", { x: 1000, y: 100, w: 80, h: 60 }, 300, { width: 2550, height: 3300 });
    expect(narrow.scaleX).toBeLessThan(1);
    expect(narrow.rect.w).toBeGreaterThan(80);
    expect(narrow.rect.x).toBeLessThan(1000);
  });

  it("grows a very short box to a legible height", () => {
    const short = layoutLabel(ctx, "[ID_1]", { x: 100, y: 100, w: 400, h: 10 }, 300, { width: 2550, height: 3300 });
    expect(short.rect.h).toBeGreaterThan(10);
    expect(short.fontPx).toBeGreaterThanOrEqual(26);
  });
});

describe("pagePixelSize", () => {
  it("lowers DPI for oversized pages", () => {
    expect(pagePixelSize(612, 792, 300, 16_000_000)).toEqual({ widthPx: 2550, heightPx: 3300, dpi: 300 });
    const big = pagePixelSize(1224, 1584, 300, 16_000_000);
    expect(big.widthPx * big.heightPx).toBeLessThanOrEqual(16_000_000);
    expect(big.dpi).toBeLessThan(300);
  });
});
