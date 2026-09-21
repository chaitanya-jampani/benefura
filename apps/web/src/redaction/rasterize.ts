import type { RedactionBox } from "@/db/dexie";

import { ctx2d, type CanvasFactory, type CanvasLike, type Ctx2D, type DecodedImage, type PageRenderer } from "./types";

export const DEFAULT_DPI = 300;
export const ALLOWED_DPI = [200, 300] as const;
export const DEFAULT_JPEG_QUALITY = 0.85;
// A clean sans-serif so server OCR keeps the token brackets.
export const LABEL_FONT_FAMILY = 'Helvetica, Arial, "Liberation Sans", "DejaVu Sans", sans-serif';

export interface RasterizedPage {
  pageIndex: number;
  jpeg: Uint8Array;
  widthPx: number;
  heightPx: number;
  dpi: number;
  quality: number;
}

export function pagePixelSize(widthPt: number, heightPt: number, dpi: number, maxPixels: number) {
  let effective = dpi;
  const area = (d: number) => ((widthPt / 72) * d) * ((heightPt / 72) * d);
  if (area(effective) > maxPixels) effective = Math.floor(dpi * Math.sqrt(maxPixels / area(dpi)));
  return {
    widthPx: Math.max(1, Math.round((widthPt / 72) * effective)),
    heightPx: Math.max(1, Math.round((heightPt / 72) * effective)),
    dpi: effective,
  };
}

/** Rounded outward so partial pixels are covered. */
export function boxToPixels(box: Pick<RedactionBox, "x" | "y" | "w" | "h">, width: number, height: number) {
  const x0 = Math.max(0, Math.floor(box.x * width));
  const y0 = Math.max(0, Math.floor(box.y * height));
  const x1 = Math.min(width, Math.ceil((box.x + box.w) * width));
  const y1 = Math.min(height, Math.ceil((box.y + box.h) * height));
  return { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) };
}

/** Rec. 601 luminance, in bands to bound memory. */
export function toGrayscale(ctx: Ctx2D, width: number, height: number): void {
  const band = 512;
  for (let y = 0; y < height; y += band) {
    const h = Math.min(band, height - y);
    const img = ctx.getImageData(0, y, width, h);
    const d = img.data;
    for (let i = 0; i < d.length; i += 4) {
      const l = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114 + 500) / 1000;
      d[i] = d[i + 1] = d[i + 2] = l;
      d[i + 3] = 255;
    }
    ctx.putImageData(img as never, 0, y);
  }
}

export interface LabelLayout {
  /** May be larger than the box so the token stays legible. */
  rect: { x: number; y: number; w: number; h: number };
  border: number;
  fontPx: number;
  scaleX: number;
}

/** Condense to 72%, then shrink to a legible minimum; only then widen the box. */
export function layoutLabel(
  ctx: Ctx2D,
  token: string,
  rect: { x: number; y: number; w: number; h: number },
  dpi: number,
  canvas: { width: number; height: number },
): LabelLayout {
  const border = Math.max(2, Math.round(dpi / 100));
  const minFont = Math.max(12, Math.round((dpi / 300) * 26));
  const r = { ...rect };

  const minHeight = Math.ceil(minFont / 0.62) + 2 * border;
  if (r.h < minHeight) {
    const grow = minHeight - r.h;
    r.y = Math.max(0, r.y - Math.floor(grow / 2));
    r.h = Math.min(canvas.height - r.y, minHeight);
  }

  const innerW = () => Math.max(1, r.w - 2 * border - Math.round(minFont * 0.4));
  let fontPx = Math.max(minFont, Math.floor((r.h - 2 * border) * 0.62));
  const measure = (px: number) => {
    ctx.font = `600 ${px}px ${LABEL_FONT_FAMILY}`;
    return ctx.measureText(token).width;
  };
  let width = measure(fontPx);
  let scaleX = 1;
  if (width > innerW()) {
    scaleX = Math.max(0.72, innerW() / width);
    if (width * scaleX > innerW()) {
      fontPx = Math.max(minFont, Math.floor((fontPx * innerW()) / (width * scaleX)));
      width = measure(fontPx);
    }
  }
  if (width * scaleX > innerW()) {
    const needed = Math.ceil(width * scaleX) + 2 * border + Math.round(minFont * 0.4);
    const grow = needed - r.w;
    r.x = Math.max(0, r.x - Math.floor(grow / 2));
    r.w = Math.min(canvas.width - r.x, needed);
  }
  return { rect: r, border, fontPx, scaleX };
}

/** Labelled boxes paint last so their tokens stay readable. */
export function burnBoxes(ctx: Ctx2D, boxes: readonly RedactionBox[], width: number, height: number, dpi: number): void {
  const enabled = boxes.filter((b) => b.enabled);
  ctx.save();
  ctx.fillStyle = "#000000";
  for (const box of enabled.filter((b) => !b.token)) {
    const r = boxToPixels(box, width, height);
    if (r.w && r.h) ctx.fillRect(r.x, r.y, r.w, r.h);
  }
  for (const box of enabled.filter((b) => b.token)) {
    const px = boxToPixels(box, width, height);
    if (!px.w || !px.h) continue;
    const layout = layoutLabel(ctx, box.token!, px, dpi, { width, height });
    const { rect, border } = layout;
    ctx.fillStyle = "#000000";
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(rect.x + border, rect.y + border, rect.w - 2 * border, rect.h - 2 * border);
    ctx.fillStyle = "#000000";
    ctx.font = `600 ${layout.fontPx}px ${LABEL_FONT_FAMILY}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.save();
    ctx.translate(rect.x + rect.w / 2, rect.y + rect.h / 2);
    ctx.scale(layout.scaleX, 1);
    ctx.fillText(box.token!, 0, 0);
    ctx.restore();
  }
  ctx.restore();
}

export interface RasterizeInput {
  renderer: PageRenderer;
  pageIndex: number;
  boxes: readonly RedactionBox[];
  factory: CanvasFactory;
  dpi?: number;
  quality?: number;
}

export async function renderToCanvas(renderer: PageRenderer, dpi: number, factory: CanvasFactory) {
  const size = pagePixelSize(renderer.widthPt, renderer.heightPt, dpi, factory.maxPixels);
  const canvas = factory.create(size.widthPx, size.heightPx);
  const ctx = ctx2d(canvas);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, size.widthPx, size.heightPx);
  await renderer.render(canvas, size.dpi / 72);
  return { canvas, ctx, ...size };
}

export async function rasterizePage(input: RasterizeInput): Promise<RasterizedPage> {
  const quality = input.quality ?? DEFAULT_JPEG_QUALITY;
  const { canvas, ctx, widthPx, heightPx, dpi } = await renderToCanvas(input.renderer, input.dpi ?? DEFAULT_DPI, input.factory);
  toGrayscale(ctx, widthPx, heightPx);
  burnBoxes(ctx, input.boxes, widthPx, heightPx, dpi);
  const jpeg = await input.factory.encodeJpeg(canvas, quality);
  releaseCanvas(canvas);
  return { pageIndex: input.pageIndex, jpeg, widthPx, heightPx, dpi, quality };
}

/** Safari keeps canvas memory until GC otherwise. */
export function releaseCanvas(canvas: CanvasLike): void {
  canvas.width = 1;
  canvas.height = 1;
}

export function imageRenderer(image: DecodedImage, widthPt: number, heightPt: number): PageRenderer {
  return {
    widthPt,
    heightPt,
    async render(canvas: CanvasLike, scale: number) {
      const ctx = ctx2d(canvas);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(image.image as never, 0, 0, Math.round(widthPt * scale), Math.round(heightPt * scale));
    },
  };
}

export async function reencodeJpeg(
  jpeg: Uint8Array,
  opts: { quality: number; scale: number | ((width: number, height: number) => number); factory: CanvasFactory },
): Promise<{ jpeg: Uint8Array; widthPx: number; heightPx: number }> {
  const decoded = await opts.factory.decode(jpeg);
  const scale = typeof opts.scale === "function" ? opts.scale(decoded.width, decoded.height) : opts.scale;
  const widthPx = Math.max(1, Math.round(decoded.width * scale));
  const heightPx = Math.max(1, Math.round(decoded.height * scale));
  const canvas = opts.factory.create(widthPx, heightPx);
  const ctx = ctx2d(canvas);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, widthPx, heightPx);
  ctx.drawImage(decoded.image as never, 0, 0, widthPx, heightPx);
  decoded.close?.();
  const out = await opts.factory.encodeJpeg(canvas, opts.quality);
  releaseCanvas(canvas);
  return { jpeg: out, widthPx, heightPx };
}

export async function thumbnailDataUrl(jpeg: Uint8Array, factory: CanvasFactory, maxWidth = 160): Promise<string> {
  const small = await reencodeJpeg(jpeg, { quality: 0.6, scale: (w) => Math.min(1, maxWidth / w), factory });
  let binary = "";
  const bytes = small.jpeg;
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:image/jpeg;base64,${btoa(binary)}`;
}
