"use client";

import { BookOpen, FileText, FileUp, ShieldCheck } from "lucide-react";
import { useId, useState, type DragEvent } from "react";

import { Button, Surface } from "@/components/ui";
import type { Region } from "@/domain/types";
import { cn } from "@/lib/cn";

import { Notice } from "./parts";

export const SAMPLE_FILES: Record<Region, { file: string; name: string }> = {
  CA: { file: "/samples/ca-northwind-booklet.pdf", name: "Northwind sample booklet" },
  AU: { file: "/samples/au-wattle-policy.pdf", name: "Wattle sample policy" },
};

const REGIONS: ReadonlyArray<{ value: Region; title: string; subtitle: string }> = [
  { value: "CA", title: "Canada", subtitle: "Extended health booklet" },
  { value: "AU", title: "Australia", subtitle: "Private health policy" },
];

export function SourceStep({
  region,
  onRegion,
  loaded,
  loading,
  error,
  onFile,
  onSample,
  onContinue,
}: {
  region: Region;
  onRegion: (region: Region) => void;
  loaded: { name: string; pages: number; isSample: boolean } | null;
  loading: string | null;
  error: string | null;
  onFile: (file: File) => void;
  onSample: () => void;
  onContinue: () => void;
}) {
  const ids = useId();
  const [dragging, setDragging] = useState(false);

  const onDrop = (e: DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) onFile(file);
  };

  return (
    <div className="flex flex-col gap-6">
      <Surface glow className="flex flex-col gap-8">
        <div className="flex flex-col gap-3">
          <h1 className="text-4xl font-medium tracking-tight text-ink sm:text-5xl">Redact your booklet</h1>
          <p className="max-w-2xl text-lg text-muted">
            Everything on these screens happens in your browser. Your file is never uploaded: at the end you&apos;ll see the exact
            page images that will be sent, with personal details painted over.
          </p>
        </div>

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-3 text-base font-medium text-ink">Where is your plan from?</legend>
          <div role="radiogroup" aria-label="Region" className="grid grid-cols-2 gap-3 sm:max-w-lg">
            {REGIONS.map((r) => {
              const checked = region === r.value;
              return (
                <button
                  key={r.value}
                  type="button"
                  role="radio"
                  aria-checked={checked}
                  onClick={() => onRegion(r.value)}
                  data-region={r.value}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowRight" || e.key === "ArrowLeft" || e.key === "ArrowDown" || e.key === "ArrowUp") {
                      e.preventDefault();
                      const other = r.value === "CA" ? "AU" : "CA";
                      onRegion(other);
                      e.currentTarget.parentElement?.querySelector<HTMLElement>(`[data-region="${other}"]`)?.focus();
                    }
                  }}
                  tabIndex={checked ? 0 : -1}
                  className={cn(
                    "flex flex-col items-start gap-0.5 rounded-tile px-5 py-4 text-left transition-[box-shadow,background-color] duration-150",
                    checked ? "bg-ink text-white" : "pill-soft text-ink",
                  )}
                >
                  <span className="text-lg font-medium">{r.title}</span>
                  <span className={cn("text-sm", checked ? "text-white/70" : "text-muted")}>{r.subtitle}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <div className="flex flex-col gap-3">
          <label
            htmlFor={`${ids}-file`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cn(
              "group flex cursor-pointer flex-col items-center gap-4 rounded-shell border border-dashed px-6 py-10 text-center transition-colors focus-within:outline-2 focus-within:outline-offset-3 focus-within:outline-focus sm:flex-row sm:text-left",
              dragging ? "border-ink bg-sunken" : "border-faint bg-surface/60 hover:bg-sunken/70",
            )}
          >
            <span aria-hidden className="grid size-16 shrink-0 place-items-center rounded-tile bg-surface text-ink shadow-tile">
              {loaded ? <FileText className="size-7" strokeWidth={1.5} /> : <FileUp className="size-7" strokeWidth={1.5} />}
            </span>
            <span className="flex min-w-0 flex-1 flex-col gap-1">
              {loaded ? (
                <>
                  <span className="truncate text-xl font-medium text-ink">{loaded.name}</span>
                  <span className="text-base text-muted">
                    {loaded.pages} {loaded.pages === 1 ? "page" : "pages"} · kept in memory only · choose another file to replace it
                  </span>
                </>
              ) : (
                <>
                  <span className="text-xl font-medium text-ink">{loading ?? "Choose a PDF or drop it here"}</span>
                  <span className="text-base text-muted">Your {region === "CA" ? "benefits booklet" : "policy document"}, up to 120 pages</span>
                </>
              )}
            </span>
            <input
              id={`${ids}-file`}
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) onFile(file);
                e.target.value = "";
              }}
            />
          </label>

          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" onClick={onSample} disabled={loading !== null}>
              <BookOpen aria-hidden className="size-4" strokeWidth={1.5} />
              Use the sample {region === "CA" ? "booklet" : "policy"}
            </Button>
            <span className="text-sm text-muted">Fictional data, handy for a walkthrough.</span>
          </div>
        </div>

        {error && <Notice tone="negative">{error}</Notice>}

        <p className="inline-flex items-start gap-2 text-base text-ink-2">
          <ShieldCheck aria-hidden className="mt-0.5 size-5 shrink-0" strokeWidth={1.5} />
          Nothing leaves this device until you tick the acknowledgment at the end.
        </p>
      </Surface>

      <div className="flex justify-end">
        <Button variant="primary" size="lg" disabled={!loaded || loading !== null} onClick={onContinue} className="w-full sm:w-auto">
          Continue
        </Button>
      </div>
    </div>
  );
}
