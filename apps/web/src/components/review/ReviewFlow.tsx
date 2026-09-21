"use client";

import { Check, FileText, LoaderCircle, RotateCcw, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button, buttonStyles, IconTile, SectionTitle, Surface } from "@/components/ui";
import { analyzeChunk, ApiError, assemblePlan, withBackoff } from "@/api/client";
import { db, setSetting, SETTING_KEYS, type DocumentRecord, type ExtractionRecord, type PageRecord } from "@/db/dexie";
import type { AnalyzeChunkResponse, Issue, PiiDetectedDetail, Plan } from "@/domain/types";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/format";
import { browserCanvasFactory } from "@/redaction/canvas";
import { categoryPhrase, fixHref } from "@/redaction/categories";
import { planChunks } from "@/redaction/chunk-pdf";

import { buildChunkUpload, MAX_CONCURRENT_CHUNKS, runPool } from "./chunk-upload";
import { IssuesPanel } from "./IssuesPanel";
import { mergeIssues, rowSources, withAliasMembers } from "./plan-edit";
import { PlanTable } from "./PlanTable";

type ChunkStatus = "waiting" | "sending" | "done" | "error";

interface ChunkState {
  index: number;
  /** 1-based booklet page numbers. */
  pages: number[];
  status: ChunkStatus;
  response?: AnalyzeChunkResponse;
  error?: { code: string; message: string; status?: number };
  details?: PiiDetectedDetail[];
}

/** Errors that stop the whole run: retrying other chunks can't succeed either. */
const BLOCKING = new Set(["ai_disabled", "budget_exhausted", "booklet_cap_exceeded", "page_cap_exceeded"]);

const BLOCKING_COPY: Record<string, { title: string; body: string }> = {
  ai_disabled: {
    title: "AI features are switched off right now",
    body: "The server isn't analyzing documents at the moment, so nothing was read. Your redacted pages are saved on this device, so you can come back and continue later.",
  },
  budget_exhausted: {
    title: "Today's demo budget is used up",
    body: "To keep this public demo free, booklet reading pauses once the daily budget runs out. Your redacted pages are saved on this device, so you can continue after it resets.",
  },
  booklet_cap_exceeded: {
    title: "You've reached today's booklet limit",
    body: "The demo reads a few booklets per visitor each day. Your redacted pages are saved on this device, so you can continue tomorrow.",
  },
  page_cap_exceeded: {
    title: "This booklet is longer than the demo allows",
    body: "The demo reads booklets of up to 120 pages. Try a shorter document, or only the pages with your benefit tables.",
  },
};

function toChunkError(err: unknown): { code: string; message: string; status?: number; details?: PiiDetectedDetail[]; retryAfter?: number | null } {
  if (err instanceof ApiError) {
    return { code: err.code, message: err.message, status: err.status, details: err.details ?? undefined, retryAfter: err.retryAfterSeconds };
  }
  return { code: "unknown", message: err instanceof Error ? err.message : "Something went wrong" };
}

function chunkLabel(pages: number[]): string {
  return pages.length === 1 ? `Page ${pages[0]}` : `Pages ${pages[0]}–${pages[pages.length - 1]}`;
}

function rowCount(r: AnalyzeChunkResponse): number {
  const rows = r.rows;
  return [rows.header, rows.benefits, rows.pools, rows.cost_shares, rows.rules, rows.hospital_categories].reduce((n, list) => n + (list?.length ?? 0), 0);
}

export function ReviewFlow() {
  const router = useRouter();
  const params = useSearchParams();
  const docId = params.get("doc");

  const [doc, setDoc] = useState<DocumentRecord | null | undefined>(undefined);
  const [chunks, setChunks] = useState<ChunkState[]>([]);
  const [blocking, setBlocking] = useState<{ code: string; retryAfter?: number | null } | null>(null);
  const [assembling, setAssembling] = useState(false);
  const [assembleError, setAssembleError] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [assembleIssues, setAssembleIssues] = useState<Issue[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const pagesRef = useRef<PageRecord[]>([]);
  const results = useRef(new Map<number, AnalyzeChunkResponse>());
  const controller = useRef<AbortController | null>(null);

  const patchChunk = useCallback((index: number, patch: Partial<ChunkState>) => {
    setChunks((all) => all.map((c) => (c.index === index ? { ...c, ...patch } : c)));
  }, []);

  const assemble = useCallback(
    async (document: DocumentRecord, total: number, signal: AbortSignal) => {
      setAssembling(true);
      setAssembleError(null);
      try {
        const ordered = [...results.current.entries()].sort(([a], [b]) => a - b).map(([, r]) => r);
        const res = await withBackoff(() => {
          signal.throwIfAborted();
          return assemblePlan(
            {
              region: document.region,
              // File names often contain the member's name; the real name stays on this device.
              documentName: document.isSample ? document.name : "Uploaded booklet",
              pageCount: document.pageCount,
              chunks: ordered.map((r) => ({ pages: r.pages, rows: r.rows })),
            },
            signal,
          );
        });
        if (signal.aborted || results.current.size !== total) return;
        setPlan({
          ...res.plan,
          id: res.plan.id || crypto.randomUUID(),
          document: { name: document.name, pageCount: document.pageCount, extractedAt: new Date().toISOString(), isDemo: false },
        });
        setAssembleIssues(res.issues ?? []);
        await db.documents.update(document.id, { status: "review", updatedAt: new Date().toISOString() });
      } catch (err) {
        if (signal.aborted) return;
        const e = toChunkError(err);
        if (BLOCKING.has(e.code)) setBlocking({ code: e.code, retryAfter: e.retryAfter });
        else setAssembleError("The extracted rows couldn't be combined into a plan. Try again in a moment.");
      } finally {
        if (!signal.aborted) setAssembling(false);
      }
    },
    [],
  );

  const run = useCallback(
    async (document: DocumentRecord, targets: ChunkState[], total: number, signal: AbortSignal) => {
      const stop = { value: false };
      await runPool(targets, MAX_CONCURRENT_CHUNKS, async (chunk) => {
        if (stop.value || signal.aborted) return;
        const id = `${document.id}:${chunk.index}`;
        const now = () => new Date().toISOString();
        patchChunk(chunk.index, { status: "sending", error: undefined, details: undefined });
        const record: ExtractionRecord = { id, documentId: document.id, chunkIndex: chunk.index, pages: chunk.pages, status: "running", updatedAt: now() };
        await db.extractions.put(record);
        try {
          const pageRecords = chunk.pages.map((p) => pagesRef.current.find((r) => r.pageIndex === p - 1)!);
          const upload = await buildChunkUpload(pageRecords, browserCanvasFactory);
          const response = await withBackoff(() => {
            signal.throwIfAborted();
            return analyzeChunk(
              { documentId: document.id, region: document.region, pages: chunk.pages, pdf: upload.pdf, thumbnails: upload.thumbnails },
              signal,
            );
          });
          if (signal.aborted) return;
          results.current.set(chunk.index, response);
          await db.extractions.put({ ...record, status: "done", response, updatedAt: now() });
          patchChunk(chunk.index, { status: "done", response });
        } catch (err) {
          if (signal.aborted) {
            await db.extractions.put({ ...record, status: "pending", updatedAt: now() });
            return;
          }
          const e = toChunkError(err);
          await db.extractions.put({ ...record, status: "error", error: { code: e.code, message: e.message, status: e.status }, updatedAt: now() });
          patchChunk(chunk.index, { status: "error", error: { code: e.code, message: e.message, status: e.status }, details: e.details });
          if (BLOCKING.has(e.code)) {
            stop.value = true;
            setBlocking({ code: e.code, retryAfter: e.retryAfter });
          }
        }
      });
      if (!signal.aborted && results.current.size === total) await assemble(document, total, signal);
    },
    [assemble, patchChunk],
  );

  useEffect(() => {
    if (!docId) return;
    const ctrl = new AbortController();
    controller.current = ctrl;
    (async () => {
      const document = await db.documents.get(docId);
      if (ctrl.signal.aborted) return;
      if (!document) {
        setDoc(null);
        return;
      }
      const [pages, records] = await Promise.all([
        db.pages.where("documentId").equals(docId).sortBy("pageIndex"),
        db.extractions.where("documentId").equals(docId).toArray(),
      ]);
      if (ctrl.signal.aborted) return;
      pagesRef.current = pages;
      const plan = planChunks(pages.length).map((indices, index) => ({ index, pages: indices.map((i) => i + 1) }));
      results.current = new Map();
      const initial: ChunkState[] = plan.map(({ index, pages: chunkPages }) => {
        const record = records.find((r) => r.chunkIndex === index && r.pages.join(",") === chunkPages.join(","));
        if (record?.status === "done" && record.response) {
          results.current.set(index, record.response);
          return { index, pages: chunkPages, status: "done", response: record.response };
        }
        return { index, pages: chunkPages, status: "waiting" };
      });
      setDoc(document);
      setChunks(initial);
      if (document.status === "acknowledged") await db.documents.update(document.id, { status: "extracting", updatedAt: new Date().toISOString() });
      await run(document, initial.filter((c) => c.status !== "done"), plan.length, ctrl.signal);
    })();
    return () => ctrl.abort();
  }, [docId, run]);

  const retry = (chunk: ChunkState) => {
    if (!doc) return;
    setBlocking(null);
    const ctrl = controller.current && !controller.current.signal.aborted ? controller.current : new AbortController();
    controller.current = ctrl;
    void run(doc, [chunk], chunks.length, ctrl.signal);
  };

  const retryAll = () => {
    if (!doc) return;
    setBlocking(null);
    const ctrl = controller.current && !controller.current.signal.aborted ? controller.current : new AbortController();
    controller.current = ctrl;
    const pending = chunks.filter((c) => c.status === "error" || c.status === "waiting");
    if (pending.length) void run(doc, pending, chunks.length, ctrl.signal);
    else void assemble(doc, chunks.length, ctrl.signal);
  };

  const save = async () => {
    if (!plan || !doc) return;
    setSaving(true);
    setSaveError(null);
    try {
      const now = new Date().toISOString();
      await db.transaction("rw", [db.plans, db.settings, db.documents, db.aliases], async () => {
        const existing = await db.plans.get(plan.id);
        const saved = withAliasMembers(plan, await db.aliases.toArray());
        await db.plans.put({ id: plan.id, plan: saved, isDemo: false, createdAt: existing?.createdAt ?? now, updatedAt: now });
        await setSetting(SETTING_KEYS.activePlanId, plan.id);
        await db.documents.update(doc.id, { status: "done", planId: plan.id, updatedAt: now });
      });
      router.push("/plan");
    } catch {
      setSaveError("The plan couldn't be saved on this device. Check that site storage isn't blocked, then try again.");
      setSaving(false);
    }
  };

  const responses = useMemo(() => chunks.flatMap((c) => (c.response ? [c.response] : [])), [chunks]);
  const sources = useMemo(() => rowSources(responses), [responses]);
  const issues = useMemo(() => mergeIssues(...responses.map((r) => r.issues), assembleIssues), [responses, assembleIssues]);
  const done = chunks.filter((c) => c.status === "done").length;

  if (!docId || doc === null) {
    return (
      <Surface className="flex flex-col items-start gap-4">
        <h1 className="text-3xl font-medium tracking-tight">Nothing to review yet</h1>
        <p className="text-lg text-muted">This link doesn&apos;t match a redacted document on this device. Start by redacting your booklet.</p>
        <Link href="/redact" className={buttonStyles({ variant: "primary" })}>
          Redact a booklet
        </Link>
      </Surface>
    );
  }

  if (doc === undefined) {
    return (
      <Surface>
        <p role="status" className="text-lg text-muted">
          Loading your document…
        </p>
      </Surface>
    );
  }

  const blockingCopy = blocking ? (BLOCKING_COPY[blocking.code] ?? BLOCKING_COPY.ai_disabled) : null;

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <Surface glow={!plan} className="flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <h1 className="text-4xl font-medium tracking-tight text-ink sm:text-5xl">{plan ? "Check your plan" : "Reading your booklet"}</h1>
          <p className="max-w-2xl text-lg text-muted">
            {doc.name} · {doc.pageCount} {doc.pageCount === 1 ? "page" : "pages"}
            {doc.acknowledgedAt ? ` · images approved ${formatDate(doc.acknowledgedAt, doc.region === "AU" ? "AUD" : "CAD")}` : ""}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-base">
            <span className="text-ink-2">
              {done} of {chunks.length} {chunks.length === 1 ? "upload" : "uploads"} read
            </span>
            {assembling && (
              <span className="inline-flex items-center gap-2 text-ink-2" role="status">
                <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" strokeWidth={1.5} />
                Building your plan
              </span>
            )}
          </div>
          <div
            role="progressbar"
            aria-label="Uploads read"
            aria-valuemin={0}
            aria-valuemax={chunks.length}
            aria-valuenow={done}
            className="h-1.5 w-full overflow-hidden rounded-full bg-line"
          >
            <div className="h-full rounded-full bg-ink transition-[width] duration-500" style={{ width: `${chunks.length ? (done / chunks.length) * 100 : 0}%` }} />
          </div>
        </div>

        {blockingCopy && (
          <div role="alert" className="flex flex-col gap-4 rounded-tile bg-sunken px-5 py-5">
            <div className="flex items-start gap-3">
              <TriangleAlert aria-hidden className="mt-1 size-5 shrink-0 text-caution" strokeWidth={1.5} />
              <div className="flex flex-col gap-1">
                <p className="text-lg font-medium text-ink">{blockingCopy.title}</p>
                <p className="text-base text-ink-2">
                  {blockingCopy.body}
                  {blocking?.retryAfter ? ` Try again in about ${Math.max(1, Math.round(blocking.retryAfter / 3600))} hour(s).` : ""}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap gap-3">
              <Link href="/" className={buttonStyles({ variant: "primary", size: "sm" })}>
                Explore the demo plan instead
              </Link>
              <Button size="sm" onClick={retryAll}>
                <RotateCcw aria-hidden className="size-4" strokeWidth={1.5} />
                Try again
              </Button>
            </div>
          </div>
        )}

        <ul className="flex flex-col gap-1" aria-label="Uploads">
          {chunks.map((chunk) => {
            const pii = chunk.error?.code === "pii_detected";
            const detail = chunk.details?.[0];
            return (
              <li key={chunk.index} className="flex flex-col gap-3 py-2 sm:flex-row sm:items-center">
                <div className="flex min-w-0 flex-1 items-center gap-4">
                  <IconTile className="size-12 sm:size-12">
                    <FileText aria-hidden className="size-5" strokeWidth={1.5} />
                  </IconTile>
                  <div className="flex min-w-0 flex-col">
                    <span className="text-lg font-medium text-ink">{chunkLabel(chunk.pages)}</span>
                    <span className={cn("text-base", chunk.status === "error" ? "text-ink-2" : "text-muted")} data-status={chunk.status}>
                      {chunk.status === "waiting" && "Waiting"}
                      {chunk.status === "sending" && "Sending and reading…"}
                      {chunk.status === "done" && chunk.response && `Done · ${rowCount(chunk.response)} ${rowCount(chunk.response) === 1 ? "row" : "rows"} found`}
                      {chunk.status === "error" &&
                        (pii
                          ? `Stopped: page ${detail?.page ?? chunk.pages[0]} still shows ${categoryPhrase(detail?.category)}`
                          : chunk.error && BLOCKING.has(chunk.error.code)
                            ? "Paused"
                            : "Couldn't be read. Nothing was saved on the server.")}
                    </span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2 pl-16 sm:pl-0">
                  {chunk.status === "sending" && <LoaderCircle aria-label="Working" className="size-5 text-muted motion-safe:animate-spin" strokeWidth={1.5} />}
                  {chunk.status === "done" && <Check aria-label="Done" className="size-5 text-positive" strokeWidth={1.5} />}
                  {chunk.status === "error" && pii && (
                    <Link href={fixHref(doc.id, detail ?? { page: chunk.pages[0], category: "", polygon: null }, chunk.pages[0])} className={buttonStyles({ size: "sm" })}>
                      Fix page {detail?.page ?? chunk.pages[0]}
                    </Link>
                  )}
                  {chunk.status === "error" && !pii && !(chunk.error && BLOCKING.has(chunk.error.code)) && (
                    <Button size="sm" onClick={() => retry(chunk)}>
                      <RotateCcw aria-hidden className="size-4" strokeWidth={1.5} />
                      Try again
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        {assembleError && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-tile bg-sunken px-4 py-3" role="alert">
            <p className="text-base text-ink-2">{assembleError}</p>
            <Button size="sm" onClick={retryAll}>
              Try again
            </Button>
          </div>
        )}
      </Surface>

      {plan && (
        <>
          <Surface className="flex flex-col gap-6">
            <SectionTitle as="h2">What we found</SectionTitle>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="flex flex-col gap-2">
                <span className="text-base font-medium text-ink">Insurer</span>
                <input
                  value={plan.insurer}
                  onChange={(e) => setPlan({ ...plan, insurer: e.target.value })}
                  className="h-12 rounded-tile bg-sunken px-4 text-base text-ink focus:bg-surface focus:outline-none focus-visible:outline-2 focus-visible:outline-focus"
                />
              </label>
              <label className="flex flex-col gap-2">
                <span className="text-base font-medium text-ink">Plan name</span>
                <input
                  value={plan.planName}
                  onChange={(e) => setPlan({ ...plan, planName: e.target.value })}
                  className="h-12 rounded-tile bg-sunken px-4 text-base text-ink focus:bg-surface focus:outline-none focus-visible:outline-2 focus-visible:outline-focus"
                />
              </label>
            </div>
            <PlanTable plan={plan} onChange={setPlan} />
          </Surface>

          {issues.length > 0 && (
            <Surface>
              <IssuesPanel issues={issues} sources={sources} documentId={doc.id} />
            </Surface>
          )}

          <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-end">
            {saveError && (
              <p role="alert" className="text-base text-negative sm:mr-auto">
                {saveError}
              </p>
            )}
            <Button variant="primary" size="lg" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save plan"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
