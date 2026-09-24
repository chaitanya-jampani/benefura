"use client";

import { Check, CircleAlert, LoaderCircle, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui";

import { piiLabel } from "./labels";

export function StatusLine({ message, state }: { message: string; state: "running" | "done" }) {
  return (
    <p className="flex items-center gap-2 text-sm text-muted" data-testid="chat-status" data-state={state}>
      {state === "running" ? (
        <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" strokeWidth={1.5} />
      ) : (
        <Check aria-hidden className="size-4 text-positive" strokeWidth={1.5} />
      )}
      <span>{message}</span>
    </p>
  );
}

export function PiiWarning({ categories, message, onEdit }: { categories: string[]; message: string; onEdit?: () => void }) {
  const labels = [...new Set(categories.map(piiLabel))];
  return (
    <section
      role="alert"
      className="flex w-full max-w-xl flex-col gap-3 rounded-tile bg-surface p-5 shadow-tile ring-1 ring-caution/25"
      data-testid="pii-warning"
    >
      <header className="flex items-center gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-caution/10 text-caution">
          <ShieldAlert aria-hidden className="size-5" strokeWidth={1.5} />
        </span>
        <h3 className="text-lg font-medium text-ink">Not sent to the assistant</h3>
      </header>
      <p className="text-ink-2">{message}</p>
      {labels.length > 0 && (
        <p className="text-sm text-muted">
          Looks like: <span className="text-ink-2">{labels.join(", ")}</span>
        </p>
      )}
      {onEdit && (
        <div>
          <Button variant="soft" size="sm" onClick={onEdit}>
            Edit message
          </Button>
        </div>
      )}
    </section>
  );
}

export function ErrorNotice({ message, onRetry, onDismiss }: { message: string; onRetry?: () => void; onDismiss: () => void }) {
  return (
    <div role="alert" className="flex w-full flex-col gap-3 rounded-tile bg-sunken p-4 sm:flex-row sm:items-center">
      <CircleAlert aria-hidden className="size-5 shrink-0 text-caution" strokeWidth={1.5} />
      <p className="flex-1 text-ink-2">{message}</p>
      <div className="flex gap-2">
        {onRetry && (
          <Button variant="soft" size="sm" onClick={onRetry}>
            Try again
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}

const API_ERROR_COPY: Record<string, string> = {
  budget_exhausted: "Today's AI budget for the demo is used up. It resets at midnight UTC, and your plan and claims still work.",
  rate_limited: "That was a lot of questions in a short time. Wait a minute and try again.",
  ai_disabled: "AI features are turned off for this demo. Your plan and claims still work.",
  payload_too_large: "This conversation is too long to send. Start a new chat.",
  invalid_request: "That message couldn't be sent. Start a new chat and try again.",
};

export function describeChatError(error: Error): string {
  const status = (error as { statusCode?: unknown }).statusCode;
  try {
    const body = JSON.parse(error.message) as { error?: { code?: string } };
    const code = body.error?.code;
    if (code && API_ERROR_COPY[code]) return API_ERROR_COPY[code];
  } catch {
    // Not a JSON error body.
  }
  if (typeof status === "number") return "Something went wrong with that answer. Try again.";
  if (error.message.startsWith("The assistant is unavailable")) return error.message;
  return "The assistant can't be reached right now. Check your connection and try again.";
}
