"use client";

import { useLiveQuery } from "dexie-react-hooks";
import { Activity, Check, Copy, FileText, Layers, MessageCircle, ReceiptText } from "lucide-react";
import { useState, type ComponentType } from "react";

import { db, type OutboundKind, type OutboundRecord } from "@/db/dexie";
import { Button, IconTile } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/format";

const KINDS: Record<OutboundKind, { label: string; Icon: ComponentType<{ className?: string; strokeWidth?: number }> }> = {
  healthz: { label: "Health check", Icon: Activity },
  "analyze-chunk": { label: "Booklet pages", Icon: FileText },
  assemble: { label: "Plan assembly", Icon: Layers },
  receipt: { label: "Receipt image", Icon: ReceiptText },
  chat: { label: "Chat message", Icon: MessageCircle },
};

function pathOf(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.host}${parsed.pathname}`;
  } catch {
    return url;
  }
}

function timeOf(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" }).format(new Date(iso));
}

function TraceId({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span className="text-muted">Trace</span>
      <code className="truncate font-mono text-xs text-ink-2" title={value}>
        {value.slice(0, 8)}…{value.slice(-4)}
      </code>
      <button
        type="button"
        className="grid size-7 shrink-0 place-items-center rounded-full text-muted hover:bg-sunken hover:text-ink"
        aria-label={copied ? "Trace id copied" : "Copy trace id"}
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? <Check className="size-3.5" strokeWidth={1.5} /> : <Copy className="size-3.5" strokeWidth={1.5} />}
      </button>
    </span>
  );
}

function OutboundRow({ row }: { row: OutboundRecord }) {
  const kind = KINDS[row.kind] ?? KINDS.chat;
  const failed = row.status !== undefined && (row.status === 0 || row.status >= 400);
  return (
    <li className="flex gap-4 py-4" data-testid="outbound-row">
      <IconTile className="size-12 sm:size-12">
        <kind.Icon className="size-5" strokeWidth={1.5} />
      </IconTile>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline justify-between gap-3">
          <span className="truncate font-medium text-ink">{kind.label}</span>
          <span className="shrink-0 text-sm text-ink-2 tabular-nums">{formatBytes(row.bytes)}</span>
        </div>
        <p className="truncate text-sm text-muted">
          {row.method} {pathOf(row.url)}
        </p>
        <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted">
          <span>{timeOf(row.at)}</span>
          {row.status !== undefined && (
            <span className={cn(failed && "text-negative")}>{row.status === 0 ? "no response" : `status ${row.status}`}</span>
          )}
          {row.durationMs !== undefined && <span>{row.durationMs} ms</span>}
        </p>
        {row.traceId && (
          <p className="text-sm">
            <TraceId value={row.traceId} />
          </p>
        )}
        {row.thumbnails.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-2" aria-label="Images sent">
            {row.thumbnails.map((src, i) => (
              <li key={i}>
                {/* eslint-disable-next-line @next/next/no-img-element -- data URLs of the exact images sent */}
                <img src={src} alt={`Sent image ${i + 1}`} className="h-20 w-auto rounded-lg border border-line bg-sunken object-contain" />
              </li>
            ))}
          </ul>
        )}
        {row.preview && (
          <details className="group mt-1">
            <summary className="w-fit cursor-pointer rounded-full text-sm text-ink-2 underline decoration-faint underline-offset-4 hover:decoration-ink">
              What was sent
            </summary>
            <pre className="mt-2 max-h-56 overflow-auto rounded-tile bg-sunken p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">
              {row.preview}
            </pre>
          </details>
        )}
      </div>
    </li>
  );
}

export function OutboundLog({ limit = 200, className }: { limit?: number; className?: string }) {
  const rows = useLiveQuery(() => db.outbound.orderBy("at").reverse().limit(limit).toArray(), [limit]);
  const total = useLiveQuery(() => db.outbound.count(), []);

  if (rows === undefined) {
    return <p className={cn("py-6 text-muted", className)}>Loading the log…</p>;
  }
  return (
    <div className={className}>
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted" aria-live="polite">
          {total === 0 ? "Nothing has left this browser yet." : `${total} ${total === 1 ? "request" : "requests"} logged on this device`}
        </p>
        {!!total && (
          <Button variant="ghost" size="sm" onClick={() => void db.outbound.clear()}>
            Clear log
          </Button>
        )}
      </div>
      {rows.length > 0 && <ul className="divide-y divide-line">{rows.map((row) => <OutboundRow key={row.id} row={row} />)}</ul>}
    </div>
  );
}
