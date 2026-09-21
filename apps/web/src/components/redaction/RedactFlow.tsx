"use client";

import { TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { buttonStyles, Surface } from "@/components/ui";
import { db, getSetting, SETTING_KEYS, type AliasRecord } from "@/db/dexie";
import type { Region } from "@/domain/types";
import { AliasBook, newId, rectFromPolygon } from "@/redaction/apply";
import { browserCanvasFactory, jpegBlob } from "@/redaction/canvas";
import { categoryPhrase, parsePolygon } from "@/redaction/categories";
import { ChunkTooLargeError, type Reduction } from "@/redaction/chunk-pdf";
import { applyFormToBook, EMPTY_FORM, formFromAliases, type HideFormValues } from "@/redaction/known";
import { saveRedactedDocument } from "@/redaction/persist";
import { ALLOWED_DPI, DEFAULT_DPI } from "@/redaction/rasterize";
import {
  closeSession,
  commitCandidates,
  detectSession,
  extractSessionText,
  openPdfSession,
  rasterizeSession,
  sessionFromStoredPages,
  sessionStore,
  type RedactionSession,
} from "@/redaction/session";
import type { BoxCandidate } from "@/redaction/types";

import { AcknowledgeStep } from "./AcknowledgeStep";
import { BoxesStep } from "./BoxesStep";
import { HideStep } from "./HideStep";
import { Notice, Stepper, type StepDef } from "./parts";
import { PreviewStep, type PreviewImage } from "./PreviewStep";
import { SAMPLE_FILES, SourceStep } from "./SourceStep";

const STEPS: readonly StepDef[] = [
  { id: 1, label: "Document" },
  { id: 2, label: "What to hide" },
  { id: 3, label: "Check boxes" },
  { id: 4, label: "Preview" },
  { id: 5, label: "Send" },
];

const MAX_PAGES = 120;
const MAX_FILE_BYTES = 150 * 1024 * 1024;

type ProgressState = { done: number; total: number; detail?: string } | null;

interface FixState {
  page: number;
  category: string | null;
  suggested: boolean;
}

function describeOpenError(err: unknown): string {
  const name = err instanceof Error ? err.name : "";
  if (name === "PasswordException") return "This PDF is password-protected. Remove the password and try again.";
  if (name === "InvalidPDFException") return "This file doesn't look like a valid PDF.";
  return "This PDF couldn't be opened. Try exporting it again as a standard PDF.";
}

export function RedactFlow() {
  const router = useRouter();
  const params = useSearchParams();
  const fixDoc = params.get("doc");
  const fixPageParam = params.get("fix");
  const fixCategory = params.get("category");
  const fixPolygon = params.get("polygon");

  const [step, setStep] = useState(fixDoc ? 3 : 1);
  const [region, setRegion] = useState<Region>("CA");
  const [session, setSession] = useState<RedactionSession | null>(null);
  const [loaded, setLoaded] = useState<{ name: string; pages: number; isSample: boolean } | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [reading, setReading] = useState<ProgressState>(null);
  const readingPromise = useRef<Promise<void> | null>(null);

  const [savedAliases, setSavedAliases] = useState<AliasRecord[]>([]);
  const [form, setForm] = useState<HideFormValues>(EMPTY_FORM);
  const [detecting, setDetecting] = useState(false);

  const [candidates, setCandidates] = useState<BoxCandidate[]>([]);
  const [pageIndex, setPageIndex] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lockedIds, setLockedIds] = useState<ReadonlySet<string>>(new Set());

  const [previews, setPreviews] = useState<PreviewImage[]>([]);
  const previewUrls = useRef<string[]>([]);
  const [reductions, setReductions] = useState<Reduction[]>([]);
  const [rasterProgress, setRasterProgress] = useState<ProgressState>(null);
  const [rasterError, setRasterError] = useState<string | null>(null);

  const [acknowledged, setAcknowledged] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [fix, setFix] = useState<FixState | null>(null);
  const [fixError, setFixError] = useState<string | null>(null);

  const headingRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    db.aliases.toArray().then((records) => {
      if (cancelled) return;
      setSavedAliases(records);
      setForm((current) => (current === EMPTY_FORM ? formFromAliases(records, newId) : current));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Preview object URLs are created in event handlers; release whatever is left on unmount.
  useEffect(() => {
    const urls = previewUrls;
    return () => {
      for (const url of urls.current) URL.revokeObjectURL(url);
      urls.current = [];
    };
  }, []);

  // A 422 from /review lands here with the page and the server's polygon.
  useEffect(() => {
    if (!fixDoc) return;
    let cancelled = false;
    (async () => {
      try {
        let s = sessionStore.get(fixDoc);
        let locked = new Set<string>();
        if (!s) {
          const doc = await db.documents.get(fixDoc);
          const pages = await db.pages.where("documentId").equals(fixDoc).toArray();
          if (!doc || pages.length === 0) throw new Error("missing");
          s = await sessionFromStoredPages(doc, pages, browserCanvasFactory);
          sessionStore.set(s);
          locked = new Set(s.candidates.map((c) => c.box.id));
        }
        if (cancelled) return;
        const page = Math.min(Math.max(1, Number(fixPageParam) || 1), s.pages.length);
        const pageRenderer = s.pages[page - 1].renderer;
        const rect = rectFromPolygon(parsePolygon(fixPolygon), { width: pageRenderer.widthPt / 72, height: pageRenderer.heightPt / 72 });
        const next = [...s.candidates];
        let selected: string | null = null;
        if (rect) {
          const suggestion: BoxCandidate = {
            box: {
              id: newId(),
              pageIndex: page - 1,
              ...rect,
              token: null,
              kind: "identifier",
              source: "server_suggestion",
              detector: fixCategory ? `server.${fixCategory}` : "server",
              confidence: 0.9,
              enabled: true,
            },
            value: "",
            label: "Suggested by the server check",
          };
          next.push(suggestion);
          selected = suggestion.box.id;
        }
        setSession(s);
        setRegion(s.region);
        setLoaded({ name: s.name, pages: s.pages.length, isSample: s.isSample });
        setCandidates(next);
        setLockedIds(locked);
        setPageIndex(page - 1);
        setSelectedId(selected);
        setFix({ page, category: fixCategory, suggested: rect !== null });
        setStep(3);
      } catch {
        if (!cancelled) setFixError("We couldn't find the saved pages for this document on this device.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fixDoc, fixPageParam, fixCategory, fixPolygon]);

  const tokens = useMemo(() => applyFormToBook(form, new AliasBook(savedAliases)).tokens, [form, savedAliases]);
  const savedTokens = useMemo(() => savedAliases.filter((a) => a.values.length > 0).map((a) => a.token), [savedAliases]);

  const goTo = (next: number) => {
    setStep(next);
    requestAnimationFrame(() => {
      headingRef.current?.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
  };

  const openDocument = async (bytes: Uint8Array, name: string, isSample: boolean) => {
    setSourceError(null);
    setLoading("Opening your document…");
    try {
      if (session) await closeSession(session);
      const s = await openPdfSession({ docId: newId(), bytes, region, name, isSample });
      if (s.pages.length > MAX_PAGES) {
        await closeSession(s);
        setSession(null);
        setLoaded(null);
        setSourceError(`This document has ${s.pages.length} pages. The demo reads booklets of up to ${MAX_PAGES} pages.`);
        return;
      }
      sessionStore.set(s);
      setSession(s);
      setLoaded({ name, pages: s.pages.length, isSample });
      setCandidates([]);
      setPreviews([]);
      setAcknowledged(false);
      setReading({ done: 0, total: s.pages.length });
      readingPromise.current = extractSessionText(s, browserCanvasFactory, (done, total, detail) =>
        setReading({ done, total, detail }),
      ).catch((err: unknown) => {
        setReading(null);
        throw err;
      });
      readingPromise.current.catch(() => undefined);
    } catch (err) {
      setSession(null);
      setLoaded(null);
      setSourceError(describeOpenError(err));
    } finally {
      setLoading(null);
    }
  };

  const onFile = async (file: File) => {
    if (file.type !== "application/pdf" && !/\.pdf$/i.test(file.name)) {
      setSourceError("Choose a PDF file. Photos of pages can be added as receipts instead.");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setSourceError("This file is larger than 150 MB, which is more than a benefits booklet should be.");
      return;
    }
    await openDocument(new Uint8Array(await file.arrayBuffer()), file.name.replace(/\.pdf$/i, ""), false);
  };

  const onSample = async () => {
    const sample = SAMPLE_FILES[region];
    setSourceError(null);
    setLoading("Loading the sample…");
    try {
      const res = await fetch(sample.file);
      if (!res.ok) throw new Error(String(res.status));
      await openDocument(new Uint8Array(await res.arrayBuffer()), sample.name, true);
    } catch {
      setLoading(null);
      setSourceError(`The sample ${region === "CA" ? "booklet" : "policy"} isn't available in this build yet. Choose your own PDF instead.`);
    }
  };

  const detect = async () => {
    if (!session) return;
    setDetecting(true);
    try {
      await readingPromise.current;
      const book = new AliasBook(savedAliases);
      const { known } = applyFormToBook(form, book);
      commitCandidates(session, candidates);
      detectSession(session, region, known, book);
      const next = [...session.candidates];
      setCandidates(next);
      const firstWithBoxes = next.length ? Math.min(...next.map((c) => c.box.pageIndex)) : 0;
      setPageIndex(firstWithBoxes);
      setSelectedId(null);
      goTo(3);
    } catch {
      setSourceError("Some pages couldn't be read. Try the file again, or export it as a new PDF.");
      goTo(1);
    } finally {
      setDetecting(false);
    }
  };

  const prepareImages = async () => {
    if (!session) return;
    commitCandidates(session, candidates);
    setAcknowledged(false);
    setRasterError(null);
    setRasterProgress({ done: 0, total: session.pages.length, detail: "Preparing page images" });
    goTo(4);
    try {
      const setting = await getSetting<number>(SETTING_KEYS.rasterDpi, DEFAULT_DPI);
      const dpi = (ALLOWED_DPI as readonly number[]).includes(setting) ? setting : DEFAULT_DPI;
      await rasterizeSession(session, {
        dpi,
        factory: browserCanvasFactory,
        onProgress: (done, total, detail) => setRasterProgress({ done, total, detail }),
      });
      for (const url of previewUrls.current) URL.revokeObjectURL(url);
      const images = session.finalPages.map((p) => ({
        pageIndex: p.pageIndex,
        url: URL.createObjectURL(jpegBlob(p.jpeg)),
        bytes: p.jpeg.byteLength,
        widthPx: p.widthPx,
        heightPx: p.heightPx,
        dpi: p.dpi,
      }));
      previewUrls.current = images.map((i) => i.url);
      setPreviews(images);
      setReductions(session.reductions);
    } catch (err) {
      setRasterError(
        err instanceof ChunkTooLargeError
          ? `${err.message}. Try the 200 DPI setting, or split the document.`
          : "The page images couldn't be prepared. Your browser may be low on memory; close other tabs and try again.",
      );
    } finally {
      setRasterProgress(null);
    }
  };

  const save = async () => {
    if (!session || !acknowledged) return;
    setSaving(true);
    setSaveError(null);
    try {
      const doc = await saveRedactedDocument({ session, form: fix ? null : form, acknowledgedAt: new Date().toISOString() });
      router.push(`/review?doc=${encodeURIComponent(doc.id)}`);
    } catch {
      setSaveError("The images couldn't be saved on this device. Check that site storage isn't blocked, then try again.");
      setSaving(false);
    }
  };

  if (fixError) {
    return (
      <Surface className="flex flex-col items-start gap-4">
        <h1 className="text-3xl font-medium tracking-tight">This document isn&apos;t on this device</h1>
        <p className="text-lg text-muted">{fixError} Start again with your PDF; nothing was kept elsewhere.</p>
        <Link href="/redact" className={buttonStyles({ variant: "primary" })}>
          Start again
        </Link>
      </Surface>
    );
  }

  const fixNotice = fix ? (
    <Notice
      tone="caution"
      icon={<TriangleAlert aria-hidden className="size-5" strokeWidth={1.5} />}
      title={`The server's check found ${categoryPhrase(fix.category)} on page ${fix.page}`}
    >
      <p>
        {fix.suggested
          ? "We've added a suggested box where it was found. Adjust it if needed, look over the rest of the page, then prepare the images again."
          : "It couldn't say exactly where. Look over the page and draw a box over anything personal, then prepare the images again."}
      </p>
    </Notice>
  ) : null;

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <div ref={headingRef} tabIndex={-1} className="outline-none">
        <Stepper steps={STEPS} current={step} />
      </div>

      {step === 1 && (
        <SourceStep
          region={region}
          onRegion={setRegion}
          loaded={loaded}
          loading={loading}
          error={sourceError}
          onFile={onFile}
          onSample={onSample}
          onContinue={() => goTo(2)}
        />
      )}

      {step === 2 && (
        <HideStep
          form={form}
          tokens={tokens}
          onChange={setForm}
          reading={reading}
          savedCount={savedTokens.length}
          busy={detecting}
          onBack={() => goTo(1)}
          onContinue={detect}
        />
      )}

      {step === 3 && session && (
        <BoxesStep
          session={session}
          candidates={candidates}
          onCandidates={setCandidates}
          pageIndex={pageIndex}
          onPageIndex={setPageIndex}
          selectedId={selectedId}
          onSelect={setSelectedId}
          lockedIds={lockedIds}
          savedTokens={savedTokens}
          notice={fixNotice}
          onBack={fix ? undefined : () => goTo(2)}
          onContinue={prepareImages}
        />
      )}
      {step === 3 && !session && (
        <Surface>
          <p className="text-lg text-muted" role="status">
            Loading saved pages…
          </p>
        </Surface>
      )}

      {step === 4 && (
        <PreviewStep
          images={previews}
          reductions={reductions}
          progress={rasterProgress}
          error={rasterError}
          onBack={() => goTo(3)}
          onContinue={() => goTo(5)}
        />
      )}

      {step === 5 && (
        <AcknowledgeStep
          images={previews}
          checked={acknowledged}
          onChecked={setAcknowledged}
          saving={saving}
          error={saveError}
          resend={fix !== null}
          onBack={() => goTo(4)}
          onContinue={save}
        />
      )}
    </div>
  );
}
