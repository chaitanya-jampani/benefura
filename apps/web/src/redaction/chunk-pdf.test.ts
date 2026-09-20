import { describe, expect, it } from "vitest";

import { AliasBook } from "./apply";
import { buildChunkPdf, ChunkTooLargeError, estimateChunkBytes, fitPagesToChunkLimit, planChunks } from "./chunk-pdf";
import { candidatesForPage } from "./detectors";
import { closePdf, extractPageText, openPdf, pdfPageRenderer } from "./pdf-text";
import { rasterizePage } from "./rasterize";
import { nodeCanvasFactory, PDFJS_NODE_ASSETS } from "./test-utils/node-canvas";
import { FAKE, makeBookletPdf } from "./test-utils/pdf";

describe("planChunks", () => {
  it("uses 5 pages with a 1-page overlap", () => {
    expect(planChunks(0)).toEqual([]);
    expect(planChunks(1)).toEqual([[0]]);
    expect(planChunks(4)).toEqual([[0, 1, 2, 3]]);
    expect(planChunks(5)).toEqual([[0, 1, 2, 3, 4]]);
    expect(planChunks(6)).toEqual([[0, 1, 2, 3, 4], [4, 5]]);
    expect(planChunks(9)).toEqual([[0, 1, 2, 3, 4], [4, 5, 6, 7, 8]]);
    expect(planChunks(10)).toEqual([[0, 1, 2, 3, 4], [4, 5, 6, 7, 8], [8, 9]]);
    expect(planChunks(13)).toEqual([[0, 1, 2, 3, 4], [4, 5, 6, 7, 8], [8, 9, 10, 11, 12]]);
  });

  it.each(Array.from({ length: 13 }, (_, i) => i + 1))("covers every page of a %i-page booklet exactly as planned", (n) => {
    const chunks = planChunks(n);
    const covered = new Set(chunks.flat());
    expect([...covered].sort((a, b) => a - b)).toEqual(Array.from({ length: n }, (_, i) => i));
    for (const chunk of chunks) expect(chunk.length).toBeLessThanOrEqual(5);
    for (let i = 1; i < chunks.length; i++) {
      expect(chunks[i][0]).toBe(chunks[i - 1].at(-1));
      // No chunk is only the overlap page.
      expect(chunks[i].length).toBeGreaterThan(1);
    }
  });

  it("rejects impossible plans", () => {
    expect(() => planChunks(3, 2, 2)).toThrow();
    expect(() => planChunks(-1)).toThrow();
  });
});

async function rasterizedBooklet(pages: number, dpi = 100) {
  const pdf = await openPdf(await makeBookletPdf({ pages }), { assetBase: PDFJS_NODE_ASSETS });
  const out = [];
  for (let i = 0; i < pages; i++) {
    const page = await pdf.getPage(i + 1);
    const text = await extractPageText(page, i);
    const boxes = candidatesForPage(text, { region: "CA", known: [{ token: "[MEMBER_A]", kind: "member", value: FAKE.name, isName: true }] }, new AliasBook()).map((c) => c.box);
    out.push(await rasterizePage({ renderer: pdfPageRenderer(page), pageIndex: i, boxes, dpi, factory: nodeCanvasFactory }));
  }
  await closePdf(pdf);
  return out;
}

describe("buildChunkPdf", () => {
  it("produces image-only pages with no text layer and no metadata", async () => {
    const images = await rasterizedBooklet(2);
    const bytes = await buildChunkPdf(images);

    const raw = Buffer.from(bytes).toString("latin1");
    for (const key of ["/Producer", "/Creator", "/Title", "/Author", "/Subject", "/Keywords", "/Metadata", "/Info", "pdf-lib", "/Type /Font", "/ToUnicode"]) {
      expect(raw).not.toContain(key);
    }
    for (const value of Object.values(FAKE)) expect(raw).not.toContain(value);

    const pdf = await openPdf(bytes, { assetBase: PDFJS_NODE_ASSETS });
    expect(pdf.numPages).toBe(2);
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const content = await page.getTextContent();
      expect(content.items).toHaveLength(0);
      const viewport = page.getViewport({ scale: 1 });
      expect(viewport.width).toBeCloseTo(612, 0);
      expect(viewport.height).toBeCloseTo(792, 0);
    }
    const meta = await pdf.getMetadata();
    const info = meta.info as Record<string, unknown>;
    for (const key of ["Title", "Author", "Subject", "Keywords", "Creator", "Producer", "CreationDate", "ModDate"]) {
      expect(info[key]).toBeUndefined();
    }
    expect(meta.metadata).toBeNull();
    await closePdf(pdf);
  });

  it("stays close to the size estimate", async () => {
    const images = await rasterizedBooklet(3);
    const bytes = await buildChunkPdf(images);
    expect(bytes.byteLength).toBeLessThanOrEqual(estimateChunkBytes(images));
  });
});

describe("fitPagesToChunkLimit", () => {
  it("leaves chunks under the cap untouched", async () => {
    const images = await rasterizedBooklet(2);
    const result = await fitPagesToChunkLimit(images, nodeCanvasFactory);
    expect(result.reductions).toEqual([]);
    expect(result.pages.map((p) => p.jpeg)).toEqual(images.map((p) => p.jpeg));
  });

  it("re-encodes the largest pages until every chunk fits, and reports it", async () => {
    const images = await rasterizedBooklet(6, 200);
    const perChunk = Math.max(estimateChunkBytes(images.slice(0, 5)), estimateChunkBytes(images.slice(4)));
    const maxBytes = Math.floor(perChunk * 0.8);
    const result = await fitPagesToChunkLimit(images, nodeCanvasFactory, { maxBytes });
    expect(result.reductions.length).toBeGreaterThan(0);
    for (const chunk of planChunks(6)) {
      const pages = chunk.map((i) => result.pages[i]);
      expect(estimateChunkBytes(pages)).toBeLessThanOrEqual(maxBytes);
      expect((await buildChunkPdf(pages)).byteLength).toBeLessThanOrEqual(maxBytes);
    }
    for (const r of result.reductions) expect(r.quality).toBeLessThan(0.85);
  });

  it("throws when a chunk cannot be made small enough", async () => {
    const images = await rasterizedBooklet(2);
    await expect(fitPagesToChunkLimit(images, nodeCanvasFactory, { maxBytes: 5_000 })).rejects.toBeInstanceOf(ChunkTooLargeError);
  });
});
