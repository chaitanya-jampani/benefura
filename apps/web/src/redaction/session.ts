// The original PDF is never persisted. A module-level map keeps it across client-side navigation to
// /review and back (422 fix); after a reload the fix flow works from the stored redacted images.
import type { PDFDocumentProxy } from "pdfjs-dist/legacy/build/pdf.mjs";

import type { DocumentRecord, PageRecord, RedactionBox } from "@/db/dexie";
import type { Region } from "@/domain/types";

import { AliasBook, mergeBoxes } from "./apply";
import { fitPagesToChunkLimit, type Reduction } from "./chunk-pdf";
import { candidatesForPage } from "./detectors";
import type { HideFormValues } from "./known";
import { ocrImage } from "./ocr";
import { closePdf, extractPageText, needsOcr, openPdf, pdfPageRenderer, visibleChars } from "./pdf-text";
import { DEFAULT_DPI, imageRenderer, rasterizePage, releaseCanvas, renderToCanvas, type RasterizedPage } from "./rasterize";
import type { BoxCandidate, CanvasFactory, KnownValue, PageRenderer, PageText } from "./types";

export interface SessionPage {
  pageIndex: number;
  renderer: PageRenderer;
  text: PageText | null;
  /** Redactions already burned in (fix flow after a reload). */
  preRedacted: boolean;
  baseDpi?: number;
}

export interface RedactionSession {
  docId: string;
  region: Region;
  name: string;
  isSample: boolean;
  pages: SessionPage[];
  candidates: BoxCandidate[];
  form: HideFormValues | null;
  rasters: Map<number, RasterizedPage & { key: string }>;
  finalPages: RasterizedPage[];
  reductions: Reduction[];
  /** Pages whose chunks must be re-sent. */
  changedSinceSave: Set<number>;
  pdf: PDFDocumentProxy | null;
}

const sessions = new Map<string, RedactionSession>();

export const sessionStore = {
  get: (docId: string) => sessions.get(docId),
  /** One session at a time so originals of abandoned uploads don't linger in memory. */
  set(session: RedactionSession) {
    for (const [id, other] of sessions) {
      if (id !== session.docId) void closeSession(other);
    }
    sessions.set(session.docId, session);
  },
  delete(docId: string) {
    const s = sessions.get(docId);
    if (s) void closeSession(s);
  },
};

export async function closeSession(session: RedactionSession): Promise<void> {
  sessions.delete(session.docId);
  session.rasters.clear();
  session.finalPages = [];
  if (session.pdf) await closePdf(session.pdf);
  session.pdf = null;
}

function emptySession(init: Pick<RedactionSession, "docId" | "region" | "name" | "isSample">): RedactionSession {
  return {
    ...init,
    pages: [],
    candidates: [],
    form: null,
    rasters: new Map(),
    finalPages: [],
    reductions: [],
    changedSinceSave: new Set(),
    pdf: null,
  };
}

export async function openPdfSession(input: {
  docId: string;
  bytes: Uint8Array;
  region: Region;
  name: string;
  isSample: boolean;
}): Promise<RedactionSession> {
  const pdf = await openPdf(input.bytes);
  const session = emptySession(input);
  session.pdf = pdf;
  for (let i = 0; i < pdf.numPages; i++) {
    const page = await pdf.getPage(i + 1);
    session.pages.push({ pageIndex: i, renderer: pdfPageRenderer(page), text: null, preRedacted: false });
  }
  return session;
}

export type ProgressFn = (done: number, total: number, detail?: string) => void;

export async function extractSessionText(
  session: RedactionSession,
  factory: CanvasFactory,
  onProgress?: ProgressFn,
  signal?: AbortSignal,
): Promise<void> {
  const total = session.pages.length;
  for (const page of session.pages) {
    signal?.throwIfAborted();
    if (page.text) continue;
    onProgress?.(page.pageIndex, total, `Reading page ${page.pageIndex + 1} of ${total}`);
    if (page.preRedacted || !session.pdf) {
      page.text = { pageIndex: page.pageIndex, widthPt: page.renderer.widthPt, heightPt: page.renderer.heightPt, runs: [], source: "none" };
      continue;
    }
    const pdfPage = await session.pdf.getPage(page.pageIndex + 1);
    let text = await extractPageText(pdfPage, page.pageIndex);
    if (needsOcr(text)) {
      const { canvas, widthPx, heightPx } = await renderToCanvas(page.renderer, DEFAULT_DPI, factory);
      try {
        const ocr = await ocrImage(
          canvas as HTMLCanvasElement,
          { width: widthPx, height: heightPx },
          { pageIndex: page.pageIndex, widthPt: page.renderer.widthPt, heightPt: page.renderer.heightPt },
          (status, p) => onProgress?.(page.pageIndex + p, total, `${status} on page ${page.pageIndex + 1} of ${total}`),
        );
        if (visibleChars(ocr) > visibleChars(text)) text = ocr;
      } finally {
        releaseCanvas(canvas);
      }
    }
    page.text = text;
  }
  onProgress?.(total, total);
}

export function commitCandidates(session: RedactionSession, candidates: readonly BoxCandidate[]): void {
  session.candidates = [...candidates];
}

/** Manual and server-suggested boxes survive a re-run. */
export function detectSession(session: RedactionSession, region: Region, known: KnownValue[], book: AliasBook): void {
  session.region = region;
  const kept = session.candidates.filter((c) => c.box.source !== "detector");
  const detected = session.pages.flatMap((page) =>
    page.text && !page.preRedacted ? candidatesForPage(page.text, { region, known }, book) : [],
  );
  session.candidates = [...detected, ...kept];
}

export function boxesForPage(session: RedactionSession, pageIndex: number): RedactionBox[] {
  return session.candidates.filter((c) => c.box.pageIndex === pageIndex).map((c) => c.box);
}

export function rasterKey(boxes: readonly RedactionBox[], dpi: number): string {
  const enabled = boxes
    .filter((b) => b.enabled)
    .map((b) => [b.x.toFixed(5), b.y.toFixed(5), b.w.toFixed(5), b.h.toFixed(5), b.token ?? ""].join(","))
    .sort();
  return `${dpi}|${enabled.join(";")}`;
}

/** `finalPages` is exactly what will be uploaded. */
export async function rasterizeSession(
  session: RedactionSession,
  opts: { dpi: number; factory: CanvasFactory; onProgress?: ProgressFn; signal?: AbortSignal },
): Promise<void> {
  const total = session.pages.length;
  for (const page of session.pages) {
    opts.signal?.throwIfAborted();
    const boxes = mergeBoxes(boxesForPage(session, page.pageIndex));
    const dpi = page.preRedacted ? (page.baseDpi ?? opts.dpi) : opts.dpi;
    const key = rasterKey(boxes, dpi);
    const existing = session.rasters.get(page.pageIndex);
    if (existing?.key === key) continue;
    opts.onProgress?.(page.pageIndex, total, `Preparing page ${page.pageIndex + 1} of ${total}`);
    const raster = await rasterizePage({ renderer: page.renderer, pageIndex: page.pageIndex, boxes, dpi, factory: opts.factory });
    session.rasters.set(page.pageIndex, { ...raster, key });
    if (existing) session.changedSinceSave.add(page.pageIndex);
  }
  opts.onProgress?.(total, total, "Checking upload sizes");
  const ordered = session.pages.map((p) => session.rasters.get(p.pageIndex)!);
  const fitted = await fitPagesToChunkLimit(ordered, opts.factory);
  session.finalPages = fitted.pages.map((p) => ({
    ...p,
    quality: fitted.reductions.find((r) => r.pageIndex === p.pageIndex)?.quality ?? session.rasters.get(p.pageIndex)!.quality,
  }));
  session.reductions = fitted.reductions;
  opts.onProgress?.(total, total);
}

export async function sessionFromStoredPages(
  doc: DocumentRecord,
  pages: readonly PageRecord[],
  factory: CanvasFactory,
): Promise<RedactionSession> {
  const session = emptySession({ docId: doc.id, region: doc.region, name: doc.name, isSample: doc.isSample });
  const sorted = [...pages].sort((a, b) => a.pageIndex - b.pageIndex);
  for (const record of sorted) {
    if (!record.image) throw new Error(`Page ${record.pageIndex + 1} has no saved image`);
    const decoded = await factory.decode(record.image);
    const widthPt = (record.widthPx / record.dpi) * 72;
    const heightPt = (record.heightPx / record.dpi) * 72;
    session.pages.push({
      pageIndex: record.pageIndex,
      renderer: imageRenderer(decoded, widthPt, heightPt),
      text: { pageIndex: record.pageIndex, widthPt, heightPt, runs: [], source: "none" },
      preRedacted: true,
      baseDpi: record.dpi,
    });
    const jpeg = new Uint8Array(await record.image.arrayBuffer());
    const boxes = record.boxes.map((b) => ({ ...b }));
    session.candidates.push(...boxes.map((box) => ({ box, value: "", label: savedLabel(box) })));
    session.rasters.set(record.pageIndex, {
      pageIndex: record.pageIndex,
      jpeg,
      widthPx: record.widthPx,
      heightPx: record.heightPx,
      dpi: record.dpi,
      quality: 0.85,
      key: rasterKey(mergeBoxes(boxes), record.dpi),
    });
  }
  return session;
}

function savedLabel(box: RedactionBox): string {
  if (box.source === "server_suggestion") return "Suggested by the server check";
  if (box.source === "manual") return "Box you drew";
  return "Saved box";
}
