"use client";

import { Maximize2, X } from "lucide-react";
import Image from "next/image";
import { useRef, useState } from "react";

import { Button, SectionTitle, Surface } from "@/components/ui";
import { formatBytes } from "@/lib/format";
import { CHUNK_OVERLAP, CHUNK_SIZE, planChunks, type Reduction } from "@/redaction/chunk-pdf";

import { Notice, Progress } from "./parts";

export interface PreviewImage {
  pageIndex: number;
  url: string;
  bytes: number;
  widthPx: number;
  heightPx: number;
  dpi: number;
}

export function PreviewStep({
  images,
  reductions,
  progress,
  error,
  onBack,
  onContinue,
}: {
  images: readonly PreviewImage[];
  reductions: readonly Reduction[];
  progress: { done: number; total: number; detail?: string } | null;
  error: string | null;
  onBack: () => void;
  onContinue: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState<PreviewImage | null>(null);
  const total = images.reduce((n, i) => n + i.bytes, 0);
  const uploads = planChunks(images.length).length;
  const dpis = [...new Set(images.map((i) => i.dpi))].sort((a, b) => b - a);
  const busy = progress !== null;

  const show = (img: PreviewImage) => {
    setOpen(img);
    dialogRef.current?.showModal();
  };

  return (
    <div className="flex flex-col gap-6">
      <Surface glow className="flex flex-col gap-6">
        <div className="flex flex-col gap-3">
          <SectionTitle as="h2">Exactly what will be sent</SectionTitle>
          <p className="max-w-2xl text-lg text-muted">
            These grayscale images are the only thing that leaves your device. They have no hidden text layer, so what you see
            here is all the server can read.
          </p>
        </div>

        {busy && <Progress label={progress.detail ?? "Preparing images"} value={progress.done} max={progress.total} />}
        {error && <Notice tone="negative">{error}</Notice>}

        {!busy && images.length > 0 && (
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Page images", String(images.length)],
              ["Total size", formatBytes(total)],
              ["Uploads", `${uploads} of up to ${CHUNK_SIZE} pages`],
              ["Resolution", `${dpis.join(" / ")} DPI`],
            ].map(([k, v]) => (
              <div key={k} className="rounded-tile bg-sunken px-4 py-3">
                <dt className="text-sm text-muted">{k}</dt>
                <dd className="text-lg font-medium text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        )}
        {!busy && uploads > 1 && (
          <p className="text-base text-muted">
            Neighbouring uploads share {CHUNK_OVERLAP} page so tables that cross a page break are read whole.
          </p>
        )}
        {!busy && reductions.length > 0 && (
          <Notice tone="muted" title="Some pages were compressed further">
            {reductions.length === 1 ? "Page " : "Pages "}
            {reductions.map((r) => r.pageIndex + 1).join(", ")} {reductions.length === 1 ? "was" : "were"} saved at lower quality
            (as low as {Math.round(Math.min(...reductions.map((r) => r.quality)) * 100)}%, {Math.min(...reductions.map((r) => r.dpi))} DPI)
            to stay under the 8 MB upload limit. The images below already reflect that.
          </Notice>
        )}

        {images.length > 0 && (
          <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4" aria-label="Page images">
            {images.map((img) => (
              <li key={img.pageIndex} className="flex flex-col gap-2">
                <button
                  type="button"
                  onClick={() => show(img)}
                  aria-label={`Enlarge page ${img.pageIndex + 1}`}
                  className="group relative overflow-hidden rounded-tile bg-surface shadow-tile"
                >
                  <Image
                    src={img.url}
                    alt={`Redacted page ${img.pageIndex + 1}`}
                    width={img.widthPx}
                    height={img.heightPx}
                    unoptimized
                    className="h-auto w-full"
                    data-page-image={img.pageIndex}
                  />
                  <span
                    aria-hidden
                    className="absolute top-2 right-2 grid size-8 place-items-center rounded-full bg-surface/90 text-ink opacity-0 shadow-tile transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
                  >
                    <Maximize2 className="size-4" strokeWidth={1.5} />
                  </span>
                </button>
                <p className="flex justify-between text-sm text-muted">
                  <span className="text-ink-2">Page {img.pageIndex + 1}</span>
                  <span>{formatBytes(img.bytes)}</span>
                </p>
              </li>
            ))}
          </ul>
        )}
      </Surface>

      <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Button variant="ghost" onClick={onBack} disabled={busy}>
          Back to boxes
        </Button>
        <Button variant="primary" size="lg" onClick={onContinue} disabled={busy || images.length === 0 || error !== null}>
          These look right
        </Button>
      </div>

      <dialog
        ref={dialogRef}
        onClose={() => setOpen(null)}
        aria-label={open ? `Page ${open.pageIndex + 1}` : "Page"}
        className="m-auto max-h-[92dvh] w-[min(56rem,94vw)] rounded-shell bg-surface p-0 shadow-float backdrop:bg-ink/40 backdrop:backdrop-blur-sm"
      >
        {open && (
          <div className="flex flex-col gap-3 p-4 sm:p-6">
            <div className="flex items-center justify-between">
              <p className="text-lg font-medium text-ink">Page {open.pageIndex + 1}</p>
              <form method="dialog">
                <Button type="submit" size="sm" aria-label="Close" className="size-9 px-0">
                  <X aria-hidden className="size-5" strokeWidth={1.5} />
                </Button>
              </form>
            </div>
            <Image
              src={open.url}
              alt={`Redacted page ${open.pageIndex + 1}, full size`}
              width={open.widthPx}
              height={open.heightPx}
              unoptimized
              className="h-auto w-full rounded-tile ring-1 ring-line"
            />
          </div>
        )}
      </dialog>
    </div>
  );
}
