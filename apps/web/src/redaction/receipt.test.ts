import { createCanvas, loadImage } from "@napi-rs/canvas";
import { describe, expect, it } from "vitest";

import { AliasBook, formatToken } from "./apply";
import { wordsFromBlocks, wordsToPageText, type OcrWord } from "./ocr";
import { detectReceipt, loadReceipt, nominalImageDpi, renderReceiptImage, type PdfReader, type ReceiptOcr } from "./receipt";
import { boxToPixels } from "./rasterize";
import { nodeCanvasFactory, sampleGrid } from "./test-utils/node-canvas";

const NOW = () => "2026-09-16T00:00:00.000Z";
const unusedPdf: PdfReader = { open: () => Promise.reject(new Error("not a PDF")) };

/** A 1200×1600 "photo" of a receipt, with the word boxes a perfect OCR would return. */
async function receiptPhoto() {
  const width = 1200;
  const height = 1600;
  const canvas = createCanvas(width, height);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#f4f1ea";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#1a1a1a";
  ctx.font = "40px sans-serif";
  const words: OcrWord[] = [];
  const lines = [["Harbour", "Physio", "Clinic"], ["Patient:", "Jordan", "Testcase"], ["Physiotherapy", "$95.00"]];
  lines.forEach((line, li) => {
    let x = 80;
    const y = 200 + li * 90;
    line.forEach((text, wi) => {
      ctx.fillText(text, x, y);
      const w = ctx.measureText(text).width;
      words.push({ text, confidence: 95, bbox: { x0: x, y0: y - 32, x1: x + w, y1: y + 8 }, lineEnd: wi === line.length - 1 });
      x += w + 20;
    });
  });
  const blob = new Blob([new Uint8Array(await canvas.encode("jpeg", 92))], { type: "image/jpeg" });
  const ocr: ReceiptOcr = async (_canvas, size, page) =>
    wordsToPageText(
      words.map((w) => ({
        ...w,
        bbox: { x0: (w.bbox.x0 * size.width) / width, x1: (w.bbox.x1 * size.width) / width, y0: (w.bbox.y0 * size.height) / height, y1: (w.bbox.y1 * size.height) / height },
      })),
      size,
      page,
    );
  return { blob, ocr, width, height };
}

describe("receipts", () => {
  it("treats a photo as a letter-length page", () => {
    expect(nominalImageDpi(3024, 4032)).toBeCloseTo(4032 / 11);
    expect(nominalImageDpi(600, 800)).toBe(150);
  });

  it("applies saved aliases automatically and burns them into a ≤4 MB image", async () => {
    const { blob, ocr } = await receiptPhoto();
    const book = new AliasBook([], NOW);
    book.assign(formatToken("MEMBER", 0), "member", "Jordan Testcase", "self");

    const source = await loadReceipt(blob, { factory: nodeCanvasFactory, ocr, pdf: unusedPdf });
    expect(source.text.source).toBe("ocr");
    const candidates = detectReceipt(source, "CA", book);
    const member = candidates.find((c) => c.box.token === "[MEMBER_A]");
    expect(member?.value).toBe("Jordan Testcase");
    // Clinic details are not the member's and are left alone.
    expect(candidates.some((c) => c.value.includes("Harbour"))).toBe(false);

    const prepared = await renderReceiptImage(source, candidates.map((c) => c.box), nodeCanvasFactory);
    expect(prepared.jpeg.byteLength).toBeLessThanOrEqual(4 * 1024 * 1024);

    const img = await loadImage(Buffer.from(prepared.jpeg));
    const canvas = createCanvas(img.width, img.height);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const { data } = ctx.getImageData(0, 0, img.width, img.height);
    const rect = boxToPixels(member!.box, img.width, img.height);
    // Top and bottom bands of the white label box contain no trace of the original glyphs.
    const band = { x: rect.x + 8, w: rect.w - 16, h: Math.max(4, Math.floor(rect.h * 0.12)) };
    const top = sampleGrid(data, img.width, { ...band, y: rect.y + 6 }, { inset: 0, step: 2 });
    expect(Math.min(...top)).toBeGreaterThan(200);
    await source.close();
  });

  it("steps quality and resolution down to meet the size cap", async () => {
    const { blob, ocr } = await receiptPhoto();
    const source = await loadReceipt(blob, { factory: nodeCanvasFactory, ocr, pdf: unusedPdf });
    const big = await renderReceiptImage(source, [], nodeCanvasFactory);
    const capped = await renderReceiptImage(source, [], nodeCanvasFactory, Math.floor(big.jpeg.byteLength / 3));
    expect(capped.jpeg.byteLength).toBeLessThanOrEqual(Math.floor(big.jpeg.byteLength / 3));
    expect(capped.dpi * capped.quality).toBeLessThan(big.dpi * big.quality);
    await expect(renderReceiptImage(source, [], nodeCanvasFactory, 500)).rejects.toThrow(/4 MB/);
  });
});

describe("OCR word conversion", () => {
  it("flattens tesseract blocks and converts pixels to page points", () => {
    const word = (text: string, x0: number) => ({ text, confidence: 90, bbox: { x0, y0: 100, x1: x0 + 50, y1: 130 }, symbols: [], choices: [], font_name: "" });
    const blocks = [{ paragraphs: [{ lines: [{ words: [word("SIN", 10), word("046", 70), word(" ", 130)] }, { words: [word("next", 10)] }] }] }];
    const words = wordsFromBlocks(blocks as never);
    expect(words.map((w) => [w.text, w.lineEnd])).toEqual([["SIN", false], ["046", true], ["next", true]]);
    const text = wordsToPageText(words, { width: 1275, height: 1650 }, { pageIndex: 2, widthPt: 612, heightPt: 792 });
    expect(text.source).toBe("ocr");
    expect(text.runs[1].ox).toBeCloseTo((70 * 612) / 1275);
    expect(text.runs[1].oy).toBeCloseTo((130 * 792) / 1650);
    expect(text.runs[1].ascent).toBeCloseTo((30 * 792) / 1650);
  });
});
