import path from "node:path";

import { createCanvas, loadImage } from "@napi-rs/canvas";
import { PDFDict, PDFDocument, PDFName, PDFRawStream, PDFRef } from "pdf-lib";

export interface Luma {
  width: number;
  height: number;
  data: Uint8ClampedArray;
}

export async function pageJpegs(pdfBytes: Uint8Array): Promise<Buffer[]> {
  const doc = await PDFDocument.load(pdfBytes, { updateMetadata: false });
  return doc.getPages().map((page, i) => {
    const resources = page.node.Resources();
    const xobjects = resources?.lookup(PDFName.of("XObject"), PDFDict);
    const images = (xobjects?.entries() ?? [])
      .map(([, ref]) => doc.context.lookup(ref instanceof PDFRef ? ref : (ref as PDFRef)))
      .filter((obj): obj is PDFRawStream => obj instanceof PDFRawStream && obj.dict.get(PDFName.of("Subtype"))?.toString() === "/Image");
    if (images.length !== 1) throw new Error(`Page ${i + 1} has ${images.length} images, expected exactly 1`);
    const filter = images[0].dict.get(PDFName.of("Filter"))?.toString();
    if (filter !== "/DCTDecode") throw new Error(`Page ${i + 1} image is ${filter}, expected JPEG`);
    return Buffer.from(images[0].contents);
  });
}

export async function pdfTextAndMetadata(pdfBytes: Uint8Array) {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const base = `${path.join(process.cwd(), "node_modules/pdfjs-dist")}/`;
  const pdf = await pdfjs.getDocument({ data: pdfBytes.slice(), standardFontDataUrl: `${base}standard_fonts/`, verbosity: 0 }).promise;
  const textItems: number[] = [];
  const pageSizes: Array<{ width: number; height: number }> = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    textItems.push((await page.getTextContent()).items.length);
    const vp = page.getViewport({ scale: 1 });
    pageSizes.push({ width: vp.width, height: vp.height });
  }
  const meta = await pdf.getMetadata();
  await pdf.loadingTask.destroy();
  return { numPages: textItems.length, textItems, pageSizes, info: meta.info as Record<string, unknown>, metadata: meta.metadata };
}

export async function decodeLuma(jpeg: Buffer): Promise<Luma> {
  const img = await loadImage(jpeg);
  const canvas = createCanvas(img.width, img.height);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(img, 0, 0);
  const rgba = ctx.getImageData(0, 0, img.width, img.height).data;
  return { width: img.width, height: img.height, data: rgba };
}

export interface PixelRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Same outward rounding as the rasterizer. */
export function boxPixels(box: { x: number; y: number; w: number; h: number }, width: number, height: number): PixelRect {
  const x0 = Math.max(0, Math.floor(box.x * width));
  const y0 = Math.max(0, Math.floor(box.y * height));
  const x1 = Math.min(width, Math.ceil((box.x + box.w) * width));
  const y1 = Math.min(height, Math.ceil((box.y + box.h) * height));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

export function samples(img: Luma, rect: PixelRect, step = 2): number[] {
  const out: number[] = [];
  for (let y = rect.y; y < rect.y + rect.h; y += step) {
    for (let x = rect.x; x < rect.x + rect.w; x += step) out.push(img.data[(y * img.width + x) * 4]);
  }
  return out;
}

export function inset(rect: PixelRect, dx: number, dy = dx): PixelRect {
  return { x: rect.x + dx, y: rect.y + dy, w: Math.max(0, rect.w - 2 * dx), h: Math.max(0, rect.h - 2 * dy) };
}
