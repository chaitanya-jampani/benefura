import type { PageRecord } from "@/db/dexie";
import { buildChunkPdf, fitPagesToChunkLimit, type ChunkImage } from "@/redaction/chunk-pdf";
import { thumbnailDataUrl } from "@/redaction/rasterize";
import type { CanvasFactory } from "@/redaction/types";

export const MAX_CONCURRENT_CHUNKS = 4;

export interface ChunkUpload {
  pdf: Blob;
  bytes: number;
  thumbnails: string[];
}

export async function buildChunkUpload(pages: readonly PageRecord[], factory: CanvasFactory): Promise<ChunkUpload> {
  const images: ChunkImage[] = [];
  for (const page of pages) {
    if (!page.image) throw new Error(`Page ${page.pageIndex + 1} has no saved image`);
    images.push({
      pageIndex: page.pageIndex,
      jpeg: new Uint8Array(await page.image.arrayBuffer()),
      widthPx: page.widthPx,
      heightPx: page.heightPx,
      dpi: page.dpi,
    });
  }
  // Safety net for pages saved by an older build or at another DPI; the redaction step already fits chunks.
  const fitted = await fitPagesToChunkLimit(images, factory, { size: images.length, overlap: 0 });
  const bytes = await buildChunkPdf(fitted.pages);
  const thumbnails: string[] = [];
  for (const img of fitted.pages) thumbnails.push(await thumbnailDataUrl(img.jpeg, factory));
  return { pdf: new Blob([bytes as BlobPart], { type: "application/pdf" }), bytes: bytes.byteLength, thumbnails };
}

export async function runPool<T>(items: readonly T[], limit: number, worker: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const lanes = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const item = items[next++];
      await worker(item);
    }
  });
  await Promise.all(lanes);
}
