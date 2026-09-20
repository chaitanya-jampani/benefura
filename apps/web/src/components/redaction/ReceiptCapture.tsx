"use client";

import { PencilLine } from "lucide-react";

import { Button } from "@/components/ui";
import type { ReceiptRecord } from "@/db/dexie";
import type { Region } from "@/domain/types";

export interface ReceiptCaptureProps {
  region: Region;
  planId: string;
  onAnalyzed: (record: ReceiptRecord) => void;
  onManual: () => void;
  onCancel: () => void;
}

export function ReceiptCapture({ onManual, onCancel }: ReceiptCaptureProps) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-base text-ink-2">
        Receipts can&apos;t be read automatically in this build yet. Enter the details from the receipt instead.
      </p>
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
  );
}
