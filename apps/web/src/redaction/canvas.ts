import type { CanvasFactory, CanvasLike, DecodedImage } from "./types";

/** Stays under iOS Safari's canvas area cap. */
export const BROWSER_MAX_PIXELS = 16_000_000;

export const browserCanvasFactory: CanvasFactory = {
  maxPixels: BROWSER_MAX_PIXELS,

  create(width: number, height: number): CanvasLike {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    // Grayscale conversion reads pixels back; a CPU-backed context makes that fast.
    canvas.getContext("2d", { willReadFrequently: true });
    return canvas;
  },

  encodeJpeg(canvas: CanvasLike, quality: number): Promise<Uint8Array> {
    return new Promise((resolve, reject) => {
      (canvas as HTMLCanvasElement).toBlob(
        (blob) => {
          if (!blob) {
            reject(new Error("The browser could not encode this page as JPEG"));
            return;
          }
          blob.arrayBuffer().then((buf) => resolve(new Uint8Array(buf)), reject);
        },
        "image/jpeg",
        quality,
      );
    });
  },

  async decode(bytes: Uint8Array | Blob): Promise<DecodedImage> {
    const blob = bytes instanceof Blob ? bytes : new Blob([bytes as BlobPart], { type: "image/jpeg" });
    const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
    return { image: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() };
  },
};

export function jpegBlob(bytes: Uint8Array): Blob {
  return new Blob([bytes as BlobPart], { type: "image/jpeg" });
}
