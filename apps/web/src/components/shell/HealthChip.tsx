"use client";

import { useHealth, type HealthState } from "@/api/useHealth";
import { Chip } from "@/components/ui";

const COPY: Record<HealthState["status"], { label: string; detail: string; tone: "muted" | "assistant" | "caution" | "negative" }> = {
  waking: {
    label: "Waking up",
    detail: "Starting the assistant. Your plan and claims already work without it.",
    tone: "muted",
  },
  ready: { label: "AI ready", detail: "The assistant is ready.", tone: "assistant" },
  ai_off: {
    label: "AI off",
    detail: "AI features are turned off for this demo. Your plan and claims still work.",
    tone: "muted",
  },
  budget_exhausted: {
    label: "Budget used",
    detail: "Today's AI budget for the demo is used up. It resets at midnight UTC; everything else still works.",
    tone: "caution",
  },
  unreachable: {
    label: "Offline",
    detail: "The assistant can't be reached. Your plan and claims still work. Select to try again.",
    tone: "negative",
  },
};

export function HealthChip() {
  const { state, refresh } = useHealth();
  const copy = COPY[state.status];
  return (
    <button
      type="button"
      onClick={() => void refresh()}
      disabled={state.status === "waking"}
      title={copy.detail}
      aria-label={`${copy.label}. ${copy.detail}`}
      className="rounded-full disabled:cursor-progress"
    >
      <span aria-live="polite" className="sr-only">
        {copy.label}
      </span>
      <Chip tone={copy.tone} className="whitespace-nowrap">
        <span aria-hidden>{copy.label}</span>
      </Chip>
    </button>
  );
}
