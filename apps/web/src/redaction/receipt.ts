import type { RedactionBox } from "@/db/dexie";
import type { Region } from "@/domain/types";

import { AliasBook, mergeBoxes } from "./apply";
import { candidatesForPage } from "./detectors";
import { burnBoxes, imageRenderer, releaseCanvas, renderToCanvas, toGrayscale } from "./rasterize";
import { ctx2d, type BoxCandidate, type CanvasFactory, type CanvasLike, type PageRenderer, type PageText, type TextRun } from "./types";

export const MAX_RECEIPT_BYTES = 4 * 1024 * 1024 - 32 * 1024;
export const RECEIPT_DPI = 300;
export const MAX_RECEIPT_PDF_PAGES = 3;

export interface ReceiptSource {
  renderer: PageRenderer;
  text: PageText;
  pageCount: number;
  truncatedPages: number;
  close: () => Promise<void>;
}

export type ReceiptOcr = (canvas: CanvasLike, size: { width: number; height: number }, page: { pageIndex: number; widthPt: number; heightPt: number }) => Promise<PageText>;

export interface PdfReader {
  open(bytes: Uint8Array): Promise<{
    numPages: number;
    page(index: number): Promise<{ renderer: PageRenderer; text: PageText }>;
    close(): Promise<void>;
  }>;
}

/** Treats a photo's long side as 11 inches. */
export function nominalImageDpi(width: number, height: number): number {
  return Math.min(600, Math.max(150, Math.max(width, height) / 11));
}

export function stackPages(
  pages: ReadonlyArray<{ renderer: PageRenderer; text: PageText }>,
  factory: CanvasFactory,
): { renderer: PageRenderer; text: PageText } {
  const widthPt = Math.max(...pages.map((p) => p.renderer.widthPt));
  const offsets: number[] = [];
  let heightPt = 0;
  for (const p of pages) {
    offsets.push(heightPt);
    heightPt += p.renderer.heightPt;
  }
  const runs: TextRun[] = pages.flatMap((p, i) => p.text.runs.map((r) => ({ ...r, oy: r.oy + offsets[i] })));
  const source = pages.some((p) => p.text.source === "ocr") ? "ocr" : pages.some((p) => p.text.source === "pdf") ? "pdf" : "none";
  return {
    text: { pageIndex: 0, widthPt, heightPt, runs, source },
    renderer: {
      widthPt,
      heightPt,
      async render(canvas: CanvasLike, scale: number) {
        const ctx = ctx2d(canvas);
        for (let i = 0; i < pages.length; i++) {
          const w = Math.max(1, Math.round(pages[i].renderer.widthPt * scale));
          const h = Math.max(1, Math.round(pages[i].renderer.heightPt * scale));
          const part = factory.create(w, h);
          const partCtx = ctx2d(part);
          partCtx.fillStyle = "#ffffff";
          partCtx.fillRect(0, 0, w, h);
          await pages[i].renderer.render(part, scale);
          ctx.drawImage(part as never, 0, Math.round(offsets[i] * scale), w, h);
          releaseCanvas(part);
        }
      },
    },
  };
}

export async function loadReceipt(
  file: Blob,
  deps: { factory: CanvasFactory; ocr: ReceiptOcr; pdf: PdfReader },
): Promise<ReceiptSource> {
  const isPdf = file.type === "application/pdf" || (file instanceof File && /\.pdf$/i.test(file.name));
  if (isPdf) {
    const doc = await deps.pdf.open(new Uint8Array(await file.arrayBuffer()));
    try {
      const count = Math.min(doc.numPages, MAX_RECEIPT_PDF_PAGES);
      const pages = [];
      for (let i = 0; i < count; i++) {
        const page = await doc.page(i);
        let text = page.text;
        if (text.runs.reduce((n, r) => n + r.str.trim().length, 0) < 24) {
          text = await ocrRenderer(page.renderer, i, deps);
        }
        pages.push({ renderer: page.renderer, text });
      }
      const stacked = pages.length === 1 ? pages[0] : stackPages(pages, deps.factory);
      // Left open for rendering until the caller calls `close`.
      return { ...stacked, pageCount: count, truncatedPages: doc.numPages - count, close: () => doc.close() };
    } catch (err) {
      await doc.close();
      throw err;
    }
  }

  const decoded = await deps.factory.decode(file);
  const dpi = nominalImageDpi(decoded.width, decoded.height);
  const widthPt = (decoded.width / dpi) * 72;
  const heightPt = (decoded.height / dpi) * 72;
  const renderer = imageRenderer(decoded, widthPt, heightPt);
  const text = await ocrRenderer(renderer, 0, deps);
  return { renderer, text, pageCount: 1, truncatedPages: 0, close: async () => decoded.close?.() };
}

async function ocrRenderer(renderer: PageRenderer, pageIndex: number, deps: { factory: CanvasFactory; ocr: ReceiptOcr }): Promise<PageText> {
  const { canvas, widthPx, heightPx } = await renderToCanvas(renderer, RECEIPT_DPI, deps.factory);
  try {
    return await deps.ocr(canvas, { width: widthPx, height: heightPx }, { pageIndex, widthPt: renderer.widthPt, heightPt: renderer.heightPt });
  } finally {
    releaseCanvas(canvas);
  }
}

export function detectReceipt(source: ReceiptSource, region: Region, book: AliasBook): BoxCandidate[] {
  return candidatesForPage(source.text, { region, known: book.knownValues() }, book);
}

export interface PreparedReceipt {
  jpeg: Uint8Array;
  widthPx: number;
  heightPx: number;
  dpi: number;
  quality: number;
}

export async function renderReceiptImage(
  source: ReceiptSource,
  boxes: readonly RedactionBox[],
  factory: CanvasFactory,
  maxBytes = MAX_RECEIPT_BYTES,
): Promise<PreparedReceipt> {
  const merged = mergeBoxes(boxes);
  const attempts: Array<{ dpi: number; quality: number }> = [
    { dpi: RECEIPT_DPI, quality: 0.85 },
    { dpi: RECEIPT_DPI, quality: 0.7 },
    { dpi: 220, quality: 0.75 },
    { dpi: 150, quality: 0.7 },
    { dpi: 110, quality: 0.65 },
  ];
  for (const attempt of attempts) {
    const { canvas, ctx, widthPx, heightPx, dpi } = await renderToCanvas(source.renderer, attempt.dpi, factory);
    toGrayscale(ctx, widthPx, heightPx);
    burnBoxes(ctx, merged, widthPx, heightPx, dpi);
    const jpeg = await factory.encodeJpeg(canvas, attempt.quality);
    releaseCanvas(canvas);
    if (jpeg.byteLength <= maxBytes) return { jpeg, widthPx, heightPx, dpi, quality: attempt.quality };
  }
  throw new Error("This receipt is still larger than 4 MB after reducing quality. Try a smaller photo.");
}
