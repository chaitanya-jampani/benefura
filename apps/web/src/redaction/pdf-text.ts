// Worker and assets come from /vendor/pdfjs because the CSP allows no CDN; the legacy build supports more browsers.
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist/legacy/build/pdf.mjs";

import type { CanvasLike, PageRenderer, PageText, TextRun } from "./types";

type Pdfjs = typeof import("pdfjs-dist/legacy/build/pdf.mjs");

export const PDFJS_VENDOR_BASE = "/vendor/pdfjs/";

let pdfjsPromise: Promise<Pdfjs> | null = null;

export function loadPdfjs(): Promise<Pdfjs> {
  pdfjsPromise ??= import("pdfjs-dist/legacy/build/pdf.mjs").then((mod) => {
    if (typeof window !== "undefined") {
      mod.GlobalWorkerOptions.workerSrc = `${PDFJS_VENDOR_BASE}pdf.worker.min.js`;
    }
    return mod;
  });
  return pdfjsPromise;
}

export interface OpenPdfOptions {
  /** Must end with a slash. */
  assetBase?: string;
}

export async function openPdf(data: Uint8Array, opts: OpenPdfOptions = {}): Promise<PDFDocumentProxy> {
  const pdfjs = await loadPdfjs();
  const base = opts.assetBase ?? PDFJS_VENDOR_BASE;
  // pdf.js transfers the buffer to its worker; pass a copy so the caller keeps its bytes.
  const task = pdfjs.getDocument({
    data: data.slice(),
    standardFontDataUrl: `${base}standard_fonts/`,
    cMapUrl: `${base}cmaps/`,
    cMapPacked: true,
    wasmUrl: `${base}wasm/`,
    iccUrl: `${base}iccs/`,
    enableXfa: false,
    verbosity: 0,
  });
  return task.promise;
}

export async function closePdf(pdf: PDFDocumentProxy): Promise<void> {
  await pdf.loadingTask.destroy();
}

interface RawTextItem {
  str: string;
  transform: number[];
  width: number;
  height: number;
  fontName: string;
  hasEOL: boolean;
}

interface RawStyle {
  ascent?: number;
  descent?: number;
  vertical?: boolean;
}

function multiply(m1: number[], m2: number[]): number[] {
  return [
    m1[0] * m2[0] + m1[2] * m2[1],
    m1[1] * m2[0] + m1[3] * m2[1],
    m1[0] * m2[2] + m1[2] * m2[3],
    m1[1] * m2[2] + m1[3] * m2[3],
    m1[0] * m2[4] + m1[2] * m2[5] + m1[4],
    m1[1] * m2[4] + m1[3] * m2[5] + m1[5],
  ];
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** `viewportTransform` maps PDF user space to a top-left origin and includes /Rotate, so rotated pages come out upright. */
export function textItemsToRuns(
  items: ReadonlyArray<RawTextItem | { type: string }>,
  styles: Record<string, RawStyle>,
  viewportTransform: number[],
): TextRun[] {
  const runs: TextRun[] = [];
  for (const item of items) {
    if (!("str" in item)) continue;
    if (!item.str) {
      if (item.hasEOL && runs.length) runs[runs.length - 1].hasEOL = true;
      continue;
    }
    const style = styles[item.fontName] ?? {};
    const tx = multiply(viewportTransform, item.transform);
    const fontHeight = Math.hypot(tx[2], tx[3]);
    const fontWidth = Math.hypot(tx[0], tx[1]);
    if (!fontHeight || !fontWidth) continue;
    const scale = Math.hypot(viewportTransform[0], viewportTransform[1]) || 1;
    let dx = tx[0] / fontWidth;
    let dy = tx[1] / fontWidth;
    let advance = item.width * scale;
    if (style.vertical) {
      // Vertical writing advances along the glyph "down" direction.
      dx = -tx[2] / fontHeight;
      dy = -tx[3] / fontHeight;
      advance = item.height * scale;
    }
    const ascent = clamp(style.ascent ?? 0.8, 0.6, 1.1) * fontHeight;
    const descent = clamp(-(style.descent ?? -0.2), 0.1, 0.4) * fontHeight;
    runs.push({ str: item.str, ox: tx[4], oy: tx[5], dx, dy, advance, ascent, descent, hasEOL: item.hasEOL });
  }
  return runs;
}

/** Below this many visible characters a page is treated as scanned. */
export const MIN_TEXT_CHARS = 24;

export function visibleChars(text: PageText): number {
  return text.runs.reduce((n, r) => n + r.str.replace(/\s/g, "").length, 0);
}

export function needsOcr(text: PageText): boolean {
  return visibleChars(text) < MIN_TEXT_CHARS;
}

export async function extractPageText(page: PDFPageProxy, pageIndex: number): Promise<PageText> {
  const viewport = page.getViewport({ scale: 1 });
  const content = await page.getTextContent();
  const runs = textItemsToRuns(
    content.items as ReadonlyArray<RawTextItem | { type: string }>,
    content.styles as Record<string, RawStyle>,
    viewport.transform,
  );
  const text: PageText = { pageIndex, widthPt: viewport.width, heightPt: viewport.height, runs, source: "pdf" };
  if (runs.length === 0) text.source = "none";
  return text;
}

export function pdfPageRenderer(page: PDFPageProxy): PageRenderer {
  const viewport = page.getViewport({ scale: 1 });
  return {
    widthPt: viewport.width,
    heightPt: viewport.height,
    async render(canvas: CanvasLike, scale: number) {
      const vp = page.getViewport({ scale });
      // The canvas may be a rounding pixel larger; harmless since pdf.js paints from the top-left.
      await page.render({ canvas: canvas as unknown as HTMLCanvasElement, viewport: vp, background: "#ffffff" }).promise;
    },
  };
}
