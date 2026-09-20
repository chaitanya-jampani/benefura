import { PDFDocument } from "pdf-lib";

import { reencodeJpeg } from "./rasterize";
import type { CanvasFactory } from "./types";

export const CHUNK_SIZE = 5;
// So a table split across pages is seen whole at least once.
export const CHUNK_OVERLAP = 1;
/** API caps the file part at 8 MiB; headroom for the multipart envelope. */
export const MAX_CHUNK_BYTES = 8 * 1024 * 1024 - 64 * 1024;

/** Page index groups: 13 pages → [0–4], [4–8], [8–12]. */
export function planChunks(pageCount: number, size = CHUNK_SIZE, overlap = CHUNK_OVERLAP): number[][] {
  if (!Number.isInteger(pageCount) || pageCount < 0) throw new Error("pageCount must be a non-negative integer");
  if (size < 1 || overlap < 0 || overlap >= size) throw new Error("overlap must be smaller than the chunk size");
  const chunks: number[][] = [];
  const stride = size - overlap;
  for (let start = 0; start < pageCount; start += stride) {
    const end = Math.min(start + size, pageCount);
    chunks.push(Array.from({ length: end - start }, (_, i) => start + i));
    if (end >= pageCount) break;
  }
  return chunks;
}

export interface ChunkImage {
  pageIndex: number;
  jpeg: Uint8Array;
  widthPx: number;
  heightPx: number;
  dpi: number;
}

/** No text layer, and `updateMetadata: false` keeps pdf-lib from writing metadata that could carry names. */
export async function buildChunkPdf(images: readonly ChunkImage[]): Promise<Uint8Array> {
  const doc = await PDFDocument.create({ updateMetadata: false });
  for (const img of images) {
    const embedded = await doc.embedJpg(img.jpeg);
    const width = (img.widthPx / img.dpi) * 72;
    const height = (img.heightPx / img.dpi) * 72;
    const page = doc.addPage([width, height]);
    page.drawImage(embedded, { x: 0, y: 0, width, height });
  }
  return doc.save({ useObjectStreams: false, addDefaultPage: false });
}

/** Upper bound: JPEG streams plus per-page PDF structure. */
export function estimateChunkBytes(images: readonly Pick<ChunkImage, "jpeg">[]): number {
  return images.reduce((n, img) => n + img.jpeg.byteLength + 1024, 1024);
}

export interface Reduction {
  pageIndex: number;
  quality: number;
  dpi: number;
}

export const REDUCTION_STEPS: ReadonlyArray<{ quality: number; scale: number }> = [
  { quality: 0.75, scale: 1 },
  { quality: 0.65, scale: 1 },
  { quality: 0.7, scale: 0.8 },
  { quality: 0.65, scale: 2 / 3 },
  { quality: 0.6, scale: 0.5 },
];

export class ChunkTooLargeError extends Error {
  constructor(readonly pages: number[], readonly bytes: number) {
    super(`Pages ${pages.map((p) => p + 1).join(", ")} are still larger than the upload limit after reducing quality`);
    this.name = "ChunkTooLargeError";
  }
}

/** Re-encodes from the original redacted JPEG each step so quality loss doesn't compound. */
export async function fitPagesToChunkLimit(
  pages: readonly ChunkImage[],
  factory: CanvasFactory,
  opts: { maxBytes?: number; size?: number; overlap?: number } = {},
): Promise<{ pages: ChunkImage[]; reductions: Reduction[] }> {
  const maxBytes = opts.maxBytes ?? MAX_CHUNK_BYTES;
  const byIndex = new Map(pages.map((p) => [p.pageIndex, p]));
  const originals = new Map(pages.map((p) => [p.pageIndex, p]));
  const step = new Map<number, number>();
  const ordered = [...pages].sort((a, b) => a.pageIndex - b.pageIndex);
  const plan = planChunks(ordered.length, opts.size, opts.overlap).map((c) => c.map((i) => ordered[i].pageIndex));

  for (;;) {
    const over = plan.find((chunk) => estimateChunkBytes(chunk.map((i) => byIndex.get(i)!)) > maxBytes);
    if (!over) break;
    const candidates = over
      .filter((i) => (step.get(i) ?? -1) < REDUCTION_STEPS.length - 1)
      .sort((a, b) => byIndex.get(b)!.jpeg.byteLength - byIndex.get(a)!.jpeg.byteLength);
    if (candidates.length === 0) {
      throw new ChunkTooLargeError(over, estimateChunkBytes(over.map((i) => byIndex.get(i)!)));
    }
    const target = candidates[0];
    const next = (step.get(target) ?? -1) + 1;
    step.set(target, next);
    const original = originals.get(target)!;
    const { quality, scale } = REDUCTION_STEPS[next];
    const out = await reencodeJpeg(original.jpeg, { quality, scale, factory });
    byIndex.set(target, {
      pageIndex: target,
      jpeg: out.jpeg,
      widthPx: out.widthPx,
      heightPx: out.heightPx,
      dpi: Math.round(original.dpi * scale),
    });
  }

  const reductions: Reduction[] = [...step.entries()]
    .sort(([a], [b]) => a - b)
    .map(([pageIndex, s]) => ({
      pageIndex,
      quality: REDUCTION_STEPS[s].quality,
      dpi: byIndex.get(pageIndex)!.dpi,
    }));
  return { pages: ordered.map((p) => byIndex.get(p.pageIndex)!), reductions };
}
