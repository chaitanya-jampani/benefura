"use client";

import { Camera, PencilLine, SquareDashedMousePointer, TriangleAlert } from "lucide-react";
import Image from "next/image";
import { useEffect, useId, useRef, useState } from "react";

import { analyzeReceipt, ApiError } from "@/api/client";
import { Button, SectionTitle } from "@/components/ui";
import { db, type AliasRecord, type RedactionBox, type ReceiptRecord } from "@/db/dexie";
import type { Region } from "@/domain/types";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/format";
import { AliasBook, compareTokens, FAMILY_KIND, newId, parseToken, rectFromPolygon } from "@/redaction/apply";
import { browserCanvasFactory, jpegBlob } from "@/redaction/canvas";
import { categoryPhrase } from "@/redaction/categories";
import { ocrImage } from "@/redaction/ocr";
import { closePdf, extractPageText, openPdf, pdfPageRenderer } from "@/redaction/pdf-text";
import { thumbnailDataUrl } from "@/redaction/rasterize";
import { detectReceipt, loadReceipt, renderReceiptImage, type PdfReader, type PreparedReceipt, type ReceiptSource } from "@/redaction/receipt";
import type { BoxCandidate, Rect } from "@/redaction/types";

import { AcknowledgeCheckbox, AzureDisclosure, BACKSTOP_TEXT } from "./AcknowledgeStep";
import { TokenSelect } from "./BoxesStep";
import { PageCanvas } from "./PageCanvas";
import { Notice, Progress, Switch } from "./parts";

export interface ReceiptCaptureProps {
  region: Region;
  planId: string;
  onAnalyzed: (record: ReceiptRecord) => void;
  onManual: () => void;
  onCancel: () => void;
}

type Stage = "pick" | "reading" | "boxes" | "preparing" | "preview" | "sending";

const pdfReader: PdfReader = {
  async open(bytes) {
    const pdf = await openPdf(bytes);
    return {
      numPages: pdf.numPages,
      async page(index) {
        const page = await pdf.getPage(index + 1);
        return { renderer: pdfPageRenderer(page), text: await extractPageText(page, index) };
      },
      close: () => closePdf(pdf),
    };
  },
};

const STOPPING_ERRORS: Record<string, string> = {
  unsafe_image: "The image safety check didn't accept this file. Enter the receipt details yourself instead.",
  ai_disabled: "AI features are switched off right now, so receipts can't be read automatically. You can enter the details yourself.",
  budget_exhausted: "Today's demo budget is used up, so receipts can't be read automatically. You can enter the details yourself.",
  rate_limited: "The server is busy. Wait a minute and send again, or enter the details yourself.",
};

export function ReceiptCapture({ region, planId, onAnalyzed, onManual, onCancel }: ReceiptCaptureProps) {
  const ids = useId();
  const [stage, setStage] = useState<Stage>("pick");
  const [progress, setProgress] = useState<{ label: string; value: number } | null>(null);
  const [candidates, setCandidates] = useState<BoxCandidate[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawMode, setDrawMode] = useState(false);
  const [prepared, setPrepared] = useState<(PreparedReceipt & { url: string }) | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState<string | null>(null);
  const [serverNotice, setServerNotice] = useState<string | null>(null);
  const [source, setSource] = useState<ReceiptSource | null>(null);
  const [savedAliases, setSavedAliases] = useState<AliasRecord[]>([]);
  const sourceRef = useRef<ReceiptSource | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    db.aliases.toArray().then((records) => {
      if (!cancelled) setSavedAliases(records);
    });
    const src = sourceRef;
    const url = urlRef;
    return () => {
      cancelled = true;
      void src.current?.close();
      if (url.current) URL.revokeObjectURL(url.current);
    };
  }, []);

  const used = [...new Set([...savedAliases.map((a) => a.token), ...candidates.flatMap((c) => (c.box.token ? [c.box.token] : []))])].sort(compareTokens);

  const pick = async (file: File) => {
    setError(null);
    setStopped(null);
    setServerNotice(null);
    if (!/^image\/(jpeg|png|webp|heic|heif)$/.test(file.type) && file.type !== "application/pdf" && !/\.(pdf|jpe?g|png)$/i.test(file.name)) {
      setError("Choose a photo (JPEG or PNG) or a PDF of the receipt.");
      return;
    }
    setStage("reading");
    setProgress({ label: "Reading the receipt on this device", value: 0 });
    try {
      await sourceRef.current?.close();
      const loaded = await loadReceipt(file, {
        factory: browserCanvasFactory,
        pdf: pdfReader,
        ocr: (canvas, size, page) =>
          ocrImage(canvas as HTMLCanvasElement, size, page, (label, value) => setProgress({ label: `${label} on this device`, value })),
      });
      sourceRef.current = loaded;
      const book = new AliasBook(savedAliases.length ? savedAliases : await db.aliases.toArray());
      setSource(loaded);
      setCandidates(detectReceipt(loaded, region, book));
      setStage("boxes");
    } catch {
      setError("This file couldn't be read. Try a clearer photo or a different PDF.");
      setStage("pick");
    } finally {
      setProgress(null);
    }
  };

  const prepare = async () => {
    if (!source) return;
    setStage("preparing");
    setAcknowledged(false);
    try {
      const out = await renderReceiptImage(source, candidates.map((c) => c.box), browserCanvasFactory);
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      const url = URL.createObjectURL(jpegBlob(out.jpeg));
      urlRef.current = url;
      setPrepared({ ...out, url });
      setStage("preview");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The image couldn't be prepared.");
      setStage("boxes");
    }
  };

  const send = async () => {
    if (!prepared || !acknowledged || !source) return;
    setStage("sending");
    setError(null);
    try {
      const thumbnail = await thumbnailDataUrl(prepared.jpeg, browserCanvasFactory);
      const res = await analyzeReceipt({ region, image: jpegBlob(prepared.jpeg), filename: "receipt.jpg", thumbnail });
      const now = new Date().toISOString();
      const record: ReceiptRecord = {
        id: newId(),
        planId,
        image: jpegBlob(prepared.jpeg),
        mime: "image/jpeg",
        width: prepared.widthPx,
        height: prepared.heightPx,
        receipt: res.receipt,
        fieldConfidence: res.fieldConfidence,
        issues: res.issues ?? [],
        traceId: res.traceId,
        source: "upload",
        createdAt: now,
      };
      const book = new AliasBook(await db.aliases.toArray());
      for (const { box, value } of candidates) {
        if (!box.enabled || !box.token || !value.trim()) continue;
        const family = parseToken(box.token)?.family;
        book.assign(box.token, family ? FAMILY_KIND[family] : box.kind, value);
      }
      const { put, remove } = book.changed();
      await db.transaction("rw", [db.receipts, db.aliases], async () => {
        await db.receipts.put(record);
        if (put.length) await db.aliases.bulkPut(put);
        if (remove.length) await db.aliases.bulkDelete(remove);
      });
      onAnalyzed(record);
    } catch (err) {
      if (err instanceof ApiError && err.code === "pii_detected") {
        const detail = err.details?.[0];
        const rect = rectFromPolygon(
          detail?.polygon,
          { width: source.renderer.widthPt / 72, height: source.renderer.heightPt / 72 },
          { width: prepared.widthPx, height: prepared.heightPx },
        );
        if (rect) {
          const box: RedactionBox = {
            id: newId(),
            pageIndex: 0,
            ...rect,
            token: null,
            kind: "identifier",
            source: "server_suggestion",
            detector: detail?.category ? `server.${detail.category}` : "server",
            confidence: 0.9,
            enabled: true,
          };
          setCandidates((all) => [...all, { box, value: "", label: "Suggested by the server check" }]);
          setSelectedId(box.id);
        }
        setServerNotice(
          `The server's check found ${categoryPhrase(detail?.category)} that wasn't hidden. ${rect ? "We've added a suggested box." : "Draw a box over it."} Nothing was saved.`,
        );
        setStage("boxes");
        return;
      }
      const code = err instanceof ApiError ? err.code : "unknown";
      if (STOPPING_ERRORS[code]) {
        setStopped(STOPPING_ERRORS[code]);
        setStage("preview");
        return;
      }
      setError("The receipt couldn't be sent. Check your connection and try again, or enter the details yourself.");
      setStage("preview");
    }
  };

  const draw = (rect: Rect) => {
    const box: RedactionBox = { id: newId(), pageIndex: 0, ...rect, token: null, kind: "custom", source: "manual", detector: "drawn", confidence: 1, enabled: true };
    setCandidates((all) => [...all, { box, value: "", label: "Box you drew" }]);
    setSelectedId(box.id);
    setDrawMode(false);
  };

  return (
    <section aria-labelledby={`${ids}-title`} className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <SectionTitle as="h2">
          <span id={`${ids}-title`}>Add a receipt</span>
        </SectionTitle>
        <p className="text-base text-muted">
          The receipt is read on this device first. Your saved labels are applied automatically, and you approve the exact image before
          it&apos;s sent.
        </p>
      </div>

      {error && <Notice tone="negative">{error}</Notice>}

      {stage === "pick" && (
        <div className="flex flex-col gap-4">
          <label
            htmlFor={`${ids}-file`}
            className="flex cursor-pointer items-center gap-4 rounded-tile border border-dashed border-faint px-5 py-6 hover:bg-sunken/70 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-focus"
          >
            <span aria-hidden className="grid size-14 shrink-0 place-items-center rounded-tile bg-surface shadow-tile">
              <Camera className="size-6" strokeWidth={1.5} />
            </span>
            <span className="flex flex-col gap-0.5">
              <span className="text-lg font-medium text-ink">Take a photo or choose a file</span>
              <span className="text-base text-muted">JPEG, PNG or PDF</span>
            </span>
            <input
              id={`${ids}-file`}
              type="file"
              accept="image/jpeg,image/png,application/pdf"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void pick(file);
                e.target.value = "";
              }}
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <Button variant="ghost" onClick={onManual}>
              <PencilLine aria-hidden className="size-4" strokeWidth={1.5} />
              Enter details myself
            </Button>
            <Button variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {(stage === "reading" || stage === "preparing") && (
        <Progress
          label={stage === "reading" ? (progress?.label ?? "Reading the receipt on this device") : "Burning in the boxes"}
          value={stage === "reading" ? (progress?.value ?? 0) : 1}
          max={1}
        />
      )}

      {stage === "boxes" && source && (
        <div className="flex flex-col gap-4">
          {serverNotice && (
            <Notice tone="caution" icon={<TriangleAlert aria-hidden className="size-5" strokeWidth={1.5} />}>
              {serverNotice}
            </Notice>
          )}
          <div className="grid grid-cols-[minmax(0,1fr)] gap-5 md:grid-cols-[minmax(0,1fr)_20rem] md:items-start">
            <div className="flex flex-col gap-3">
              <Button size="sm" aria-pressed={drawMode} onClick={() => setDrawMode((d) => !d)} className={cn("self-start", drawMode && "bg-ink text-white")}>
                <SquareDashedMousePointer aria-hidden className="size-4" strokeWidth={1.5} />
                {drawMode ? "Drawing: drag on the receipt" : "Draw a box"}
              </Button>
              <PageCanvas
                renderer={source.renderer}
                label="Receipt"
                candidates={candidates}
                selectedId={selectedId}
                onSelect={setSelectedId}
                drawMode={drawMode}
                onDraw={draw}
                onMove={(id, rect) => setCandidates((all) => all.map((c) => (c.box.id === id ? { ...c, box: { ...c.box, ...rect } } : c)))}
              />
              {source.truncatedPages > 0 && <p className="text-sm text-muted">Only the first {source.pageCount} pages are used.</p>}
            </div>
            <div className="flex flex-col gap-3">
              <p className="text-base text-ink-2">
                {candidates.filter((c) => c.box.enabled).length === 0
                  ? "Nothing personal was found. Provider names and addresses are fine to leave; draw a box over anything else."
                  : "Clinic details can stay visible. Check that your name and numbers are covered."}
              </p>
              <ul className="-mx-2 flex flex-col gap-1">
                {candidates.map((c) => (
                  <li key={c.box.id} className={cn("flex items-center gap-3 rounded-tile px-2 py-2", c.box.id === selectedId && "bg-sunken ring-1 ring-focus")}>
                    <Switch
                      checked={c.box.enabled}
                      label={`Hide ${c.value || c.label}`}
                      onChange={(enabled) => setCandidates((all) => all.map((x) => (x.box.id === c.box.id ? { ...x, box: { ...x.box, enabled } } : x)))}
                    />
                    <button type="button" onClick={() => setSelectedId(c.box.id)} className="flex min-w-0 flex-1 flex-col items-start text-left">
                      <span className="w-full truncate text-base text-ink">{c.value || c.label}</span>
                      <span className="w-full truncate text-sm text-muted">{c.value ? c.label : "Drawn or suggested"}</span>
                    </button>
                    <TokenSelect
                      value={c.box.token}
                      used={used}
                      label={`Label for ${c.value || c.label}`}
                      onChange={(token) => setCandidates((all) => all.map((x) => (x.box.id === c.box.id ? { ...x, box: { ...x.box, token } } : x)))}
                    />
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
            <Button variant="ghost" onClick={onManual}>
              Enter details myself
            </Button>
            <Button variant="primary" onClick={prepare}>
              Prepare image
            </Button>
          </div>
        </div>
      )}

      {(stage === "preview" || stage === "sending") && prepared && (
        <div className="flex flex-col gap-5">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-5 md:grid-cols-[16rem_minmax(0,1fr)] md:items-start">
            <figure className="flex flex-col gap-2">
              <Image
                src={prepared.url}
                alt="Redacted receipt image that will be sent"
                width={prepared.widthPx}
                height={prepared.heightPx}
                unoptimized
                className="h-auto w-full rounded-tile bg-surface shadow-tile ring-1 ring-line"
              />
              <figcaption className="text-sm text-muted">
                Exactly what will be sent · {formatBytes(prepared.jpeg.byteLength)} · {prepared.dpi} DPI grayscale
              </figcaption>
            </figure>
            <div className="flex flex-col gap-4">
              <AzureDisclosure subject="this receipt image" />
              <Notice tone="muted">{BACKSTOP_TEXT}</Notice>
            </div>
          </div>
          {stopped && (
            <Notice tone="caution" icon={<TriangleAlert aria-hidden className="size-5" strokeWidth={1.5} />}>
              {stopped}
            </Notice>
          )}
          <AcknowledgeCheckbox checked={acknowledged} onChange={setAcknowledged}>
            I&apos;ve checked this image and I&apos;m OK sending it for analysis.
          </AcknowledgeCheckbox>
          <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap gap-2">
              <Button variant="ghost" onClick={() => setStage("boxes")} disabled={stage === "sending"}>
                Back to boxes
              </Button>
              <Button variant="ghost" onClick={onManual} disabled={stage === "sending"}>
                Enter details myself
              </Button>
            </div>
            <Button variant="primary" onClick={send} disabled={!acknowledged || stage === "sending" || stopped !== null}>
              {stage === "sending" ? "Sending…" : "Send receipt"}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
