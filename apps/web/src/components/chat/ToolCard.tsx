"use client";

import { getToolName, type DynamicToolUIPart, type ToolUIPart } from "ai";
import {
  BookOpen,
  Calculator,
  ChartNoAxesColumn,
  ChevronDown,
  CircleAlert,
  Check,
  FilePen,
  FilePlus,
  FileSearch,
  Library,
  ListChecks,
  LoaderCircle,
  Search,
  type LucideIcon,
} from "lucide-react";

import type { ChatTools, ToolName } from "@/agent/types";
import { cn } from "@/lib/cn";

import { toolLabel } from "./labels";

const ICONS: Record<ToolName, LucideIcon> = {
  get_plan_overview: BookOpen,
  find_benefits: Search,
  search_plan_document: FileSearch,
  get_usage: ChartNoAxesColumn,
  estimate_reimbursement: Calculator,
  list_claims: ListChecks,
  draft_claim: FilePlus,
  update_claim: FilePen,
  ask_knowledge_agent: Library,
  search_public_knowledge: Library,
};

export type AnyToolPart = ToolUIPart<ChatTools> | DynamicToolUIPart;

function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-60 overflow-auto rounded-xl bg-sunken p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function ToolCard({ part }: { part: AnyToolPart }) {
  const name = getToolName(part);
  const Icon = ICONS[name as ToolName] ?? Search;
  const done = part.state === "output-available";
  const failed = part.state === "output-error" || part.state === "output-denied";
  const running = !done && !failed;
  const server = part.providerExecuted === true;

  return (
    <details className="group/tool w-full max-w-xl rounded-tile bg-sunken/70" data-testid={`tool-${name}`} data-state={part.state}>
      <summary className="flex cursor-pointer list-none items-center gap-3 rounded-tile px-3 py-2.5 text-sm text-ink-2 [&::-webkit-details-marker]:hidden">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-surface text-ink shadow-tile">
          <Icon aria-hidden className="size-4" strokeWidth={1.5} />
        </span>
        <span className="min-w-0 flex-1 truncate">{toolLabel(name, !running)}</span>
        <span className="sr-only">{running ? "In progress" : failed ? "Failed" : "Done"}</span>
        {running && <LoaderCircle aria-hidden className="size-4 text-muted motion-safe:animate-spin" strokeWidth={1.5} />}
        {done && <Check aria-hidden className="size-4 text-positive" strokeWidth={1.5} />}
        {failed && <CircleAlert aria-hidden className="size-4 text-caution" strokeWidth={1.5} />}
        <ChevronDown aria-hidden className="size-4 text-muted transition-transform group-open/tool:rotate-180" strokeWidth={1.5} />
      </summary>
      <div className="flex flex-col gap-3 px-3 pb-3 text-sm">
        {part.input !== undefined && (
          <div className="flex flex-col gap-1.5">
            <span className="text-muted">Requested by the assistant</span>
            <Json value={part.input} />
          </div>
        )}
        {part.state === "output-available" && (
          <div className="flex flex-col gap-1.5">
            <span className="text-muted">{server ? "Returned by the Benefura API" : "Shared with the assistant from this browser"}</span>
            <Json value={part.output} />
          </div>
        )}
        {part.state === "output-error" && (
          <p className={cn("rounded-xl bg-surface p-3 text-ink-2")}>{part.errorText}</p>
        )}
      </div>
    </details>
  );
}
