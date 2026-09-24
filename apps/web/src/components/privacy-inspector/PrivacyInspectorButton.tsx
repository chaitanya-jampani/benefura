"use client";

import { useLiveQuery } from "dexie-react-hooks";
import { ScanEye, X } from "lucide-react";
import Link from "next/link";
import { useRef } from "react";

import { db } from "@/db/dexie";

import { OutboundLog } from "./OutboundLog";

export function PrivacyInspectorButton() {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const count = useLiveQuery(() => db.outbound.count(), [], 0);

  const open = () => dialogRef.current?.showModal();
  const close = () => dialogRef.current?.close();

  return (
    <>
      <button
        type="button"
        onClick={open}
        aria-haspopup="dialog"
        aria-label={`Privacy inspector: ${count} ${count === 1 ? "request" : "requests"} sent from this browser`}
        className="pill-soft relative grid size-12 shrink-0 place-items-center rounded-full text-ink transition-transform active:scale-95"
      >
        <ScanEye aria-hidden className="size-6" strokeWidth={1.5} />
        {count > 0 && (
          <span
            aria-hidden
            className="absolute -top-0.5 -right-0.5 grid h-5 min-w-5 place-items-center rounded-full bg-ink px-1 text-[0.6875rem] font-medium text-white tabular-nums"
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      <dialog
        ref={dialogRef}
        aria-labelledby="privacy-inspector-title"
        onClick={(e) => {
          if (e.target === e.currentTarget) close();
        }}
        className="no-print fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-dvh w-full max-w-lg overflow-hidden bg-transparent p-0 backdrop:bg-ink/15 sm:p-3"
      >
        <div className="flex h-full flex-col bg-surface shadow-float sm:rounded-shell">
          <header className="flex items-start justify-between gap-4 px-6 pt-6 pb-2 sm:px-8 sm:pt-8">
            <div className="flex flex-col gap-1">
              <h2 id="privacy-inspector-title" className="text-2xl font-medium tracking-tight">
                Sent from this browser
              </h2>
              <p className="text-base text-muted">
                Every request to the Benefura API, with exactly what it carried. Original files, your alias map and
                your records stay on this device.
              </p>
            </div>
            <button
              type="button"
              onClick={close}
              aria-label="Close privacy inspector"
              className="pill-soft grid size-11 shrink-0 place-items-center rounded-full"
            >
              <X aria-hidden className="size-5" strokeWidth={1.5} />
            </button>
          </header>
          <div className="flex-1 overflow-y-auto px-6 pb-4 sm:px-8">
            <OutboundLog limit={100} />
          </div>
          <footer className="border-t border-line px-6 py-4 sm:px-8">
            <Link href="/privacy" onClick={close} className="rounded-full text-ink-2 underline decoration-faint underline-offset-4 hover:decoration-ink">
              How Benefura handles your data
            </Link>
          </footer>
        </div>
      </dialog>
    </>
  );
}
