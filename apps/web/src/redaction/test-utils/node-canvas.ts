import path from "node:path";

import { createCanvas, loadImage, type Canvas } from "@napi-rs/canvas";

import type { CanvasFactory, CanvasLike, DecodedImage } from "../types";

export const nodeCanvasFactory: CanvasFactory = {
  maxPixels: 40_000_000,
  create(width: number, height: number): CanvasLike {
    return createCanvas(width, height) as unknown as CanvasLike;
  },
  async encodeJpeg(canvas: CanvasLike, quality: number): Promise<Uint8Array> {
    const buf = await (canvas as unknown as Canvas).encode("jpeg", Math.round(quality * 100));
    return new Uint8Array(buf);
  },
  async decode(bytes: Uint8Array | Blob): Promise<DecodedImage> {
    const data = bytes instanceof Blob ? new Uint8Array(await bytes.arrayBuffer()) : bytes;
    const image = await loadImage(Buffer.from(data));
    return { image, width: image.width, height: image.height };
  },
};

export const PDFJS_NODE_ASSETS = `${path.join(process.cwd(), "node_modules/pdfjs-dist")}/`;

export function sampleGrid(
  data: Uint8ClampedArray,
  width: number,
  rect: { x: number; y: number; w: number; h: number },
  opts: { inset?: number; step?: number } = {},
): number[] {
  const inset = opts.inset ?? 2;
  const step = opts.step ?? 3;
  const out: number[] = [];
  for (let y = rect.y + inset; y < rect.y + rect.h - inset; y += step) {
    for (let x = rect.x + inset; x < rect.x + rect.w - inset; x += step) {
      out.push(data[(y * width + x) * 4]);
    }
  }
  return out;
}
