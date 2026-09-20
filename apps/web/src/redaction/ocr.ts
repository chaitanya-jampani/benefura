// Worker, wasm core and English model are served from /vendor/tesseract: no CDN, and images never leave the browser.
import type Tesseract from "tesseract.js";

import type { PageText, TextRun } from "./types";

export const TESSERACT_VENDOR_BASE = "/vendor/tesseract/";

export interface OcrWord {
  text: string;
  confidence: number;
  bbox: { x0: number; y0: number; x1: number; y1: number };
  lineEnd: boolean;
}

export type OcrProgress = (status: string, progress: number) => void;

let workerPromise: Promise<Tesseract.Worker> | null = null;
let progressListener: OcrProgress | null = null;

function absolute(path: string): string {
  return new URL(path, window.location.origin).href;
}

const STATUS_TEXT: Record<string, string> = {
  "loading tesseract core": "Loading the text reader",
  "initializing tesseract": "Starting the text reader",
  "loading language traineddata": "Loading the English model",
  "initializing api": "Starting the text reader",
  "recognizing text": "Reading text",
};

export function getOcrWorker(): Promise<Tesseract.Worker> {
  workerPromise ??= (async () => {
    // tesseract.js is CommonJS; depending on the bundler its API is on the namespace or on `default`.
    const mod = (await import("tesseract.js")) as unknown as typeof Tesseract & { default?: typeof Tesseract };
    const { createWorker } = mod.default ?? mod;
    const base = TESSERACT_VENDOR_BASE;
    const LSTM_ONLY = 1 as Tesseract.OEM;
    const worker = await createWorker("eng", LSTM_ONLY, {
      workerPath: absolute(`${base}worker.min.js`),
      corePath: absolute(`${base}core`),
      langPath: absolute(`${base}lang`),
      workerBlobURL: false,
      cacheMethod: "none",
      gzip: true,
      logger: (m) => progressListener?.(STATUS_TEXT[m.status] ?? "Reading text", m.progress),
    });
    await worker.setParameters({
      tessedit_pageseg_mode: "3" as Tesseract.PSM,
      preserve_interword_spaces: "1",
      user_defined_dpi: "300",
    });
    return worker;
  })().catch((err) => {
    workerPromise = null;
    throw err;
  });
  return workerPromise;
}

export async function terminateOcr(): Promise<void> {
  const pending = workerPromise;
  workerPromise = null;
  if (pending) await (await pending).terminate();
}

export function wordsFromBlocks(blocks: Tesseract.Block[] | null): OcrWord[] {
  const words: OcrWord[] = [];
  for (const block of blocks ?? []) {
    for (const paragraph of block.paragraphs) {
      for (const line of paragraph.lines) {
        const visible = line.words.filter((w) => w.text.trim());
        visible.forEach((w, i) => {
          words.push({ text: w.text.trim(), confidence: w.confidence, bbox: w.bbox, lineEnd: i === visible.length - 1 });
        });
      }
    }
  }
  return words;
}

/** Image pixels → page points. */
export function wordsToPageText(
  words: readonly OcrWord[],
  image: { width: number; height: number },
  page: { pageIndex: number; widthPt: number; heightPt: number },
): PageText {
  const sx = page.widthPt / image.width;
  const sy = page.heightPt / image.height;
  const runs: TextRun[] = words.map((w) => ({
    str: w.text,
    ox: w.bbox.x0 * sx,
    oy: w.bbox.y1 * sy,
    dx: 1,
    dy: 0,
    advance: Math.max(1, (w.bbox.x1 - w.bbox.x0) * sx),
    ascent: Math.max(1, (w.bbox.y1 - w.bbox.y0) * sy),
    descent: 0,
    hasEOL: w.lineEnd,
  }));
  return { pageIndex: page.pageIndex, widthPt: page.widthPt, heightPt: page.heightPt, runs, source: runs.length ? "ocr" : "none" };
}

export async function ocrImage(
  image: HTMLCanvasElement | Blob,
  size: { width: number; height: number },
  page: { pageIndex: number; widthPt: number; heightPt: number },
  onProgress?: OcrProgress,
): Promise<PageText> {
  const worker = await getOcrWorker();
  progressListener = onProgress ?? null;
  try {
    const { data } = await worker.recognize(image, {}, { blocks: true, text: false });
    return wordsToPageText(wordsFromBlocks(data.blocks), size, page);
  } finally {
    progressListener = null;
  }
}
