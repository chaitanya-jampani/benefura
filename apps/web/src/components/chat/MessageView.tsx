"use client";

import { isToolUIPart } from "ai";

import { ApprovalCard } from "@/agent/ApprovalCard";
import { Citations, uniqueCitations } from "@/agent/Citations";
import { APPROVAL_TOOLS } from "@/agent/tools";
import type { BenefuraUIMessage, Citation } from "@/agent/types";
import { Orb } from "@/components/ui";
import type { Plan } from "@/domain/types";
import { cn } from "@/lib/cn";

import { AGENT_LABELS } from "./labels";
import { Markdown } from "./Markdown";
import { PiiWarning, StatusLine } from "./Notices";
import { ToolCard, type AnyToolPart } from "./ToolCard";

export interface MessageViewProps {
  message: BenefuraUIMessage;
  plan: Plan | null;
  notSent?: boolean;
  /** Latest message and the chat is idle, so approval cards can be answered. */
  interactive: boolean;
  streaming: boolean;
  busyToolCallId: string | null;
  onApprove: (message: BenefuraUIMessage, part: AnyToolPart) => void;
  onDecline: (part: AnyToolPart, reason: string) => void;
  onEditBlocked: (assistantMessageId: string) => void;
}

function toolName(part: AnyToolPart): string {
  return part.type === "dynamic-tool" ? part.toolName : part.type.slice("tool-".length);
}

export function MessageView({ message, plan, notSent, interactive, streaming, busyToolCallId, onApprove, onDecline, onEditBlocked }: MessageViewProps) {
  if (message.role === "user") {
    const text = message.parts.map((p) => (p.type === "text" ? p.text : "")).join("\n");
    return (
      <li className="flex flex-col items-end gap-1.5" data-testid="user-message">
        <p className="sr-only">You said:</p>
        <div className="max-w-[85%] rounded-[1.5rem] rounded-br-md bg-sunken px-5 py-3 text-base whitespace-pre-wrap text-ink sm:max-w-[75%] sm:text-lg">
          {text}
        </div>
        {notSent && <span className="text-sm text-muted">Not sent to the assistant</span>}
      </li>
    );
  }

  const citations: Citation[][] = [];
  const blocks: React.ReactNode[] = [];
  message.parts.forEach((part, i) => {
    const key = `${message.id}-${i}`;
    if (part.type === "text") {
      if (part.text.trim()) blocks.push(<Markdown key={key} text={part.text} />);
      return;
    }
    if (part.type === "data-status") {
      blocks.push(<StatusLine key={part.id ?? key} message={part.data.message} state={part.data.state} />);
      return;
    }
    if (part.type === "data-citations") {
      citations.push(part.data.citations);
      return;
    }
    if (part.type === "data-pii-warning") {
      blocks.push(
        <PiiWarning key={key} categories={part.data.categories} message={part.data.message} onEdit={interactive ? () => onEditBlocked(message.id) : undefined} />,
      );
      return;
    }
    if (isToolUIPart(part)) {
      const toolPart = part as AnyToolPart;
      if (APPROVAL_TOOLS.has(toolName(toolPart)) && toolPart.state !== "input-streaming" && toolPart.state !== "input-available") {
        blocks.push(
          <ApprovalCard
            key={toolPart.toolCallId}
            part={toolPart}
            plan={plan}
            active={interactive}
            busy={busyToolCallId === toolPart.toolCallId}
            onApprove={() => onApprove(message, toolPart)}
            onDecline={(reason) => onDecline(toolPart, reason)}
          />,
        );
      } else {
        blocks.push(<ToolCard key={toolPart.toolCallId} part={toolPart} />);
      }
    }
  });

  const agent = message.metadata?.agent;
  const version = message.metadata?.agentVersion;
  const merged = uniqueCitations(citations);

  return (
    <li className="flex gap-3 sm:gap-4" data-testid="assistant-message">
      <Orb size={32} className={cn("mt-0.5 hidden shadow-none sm:inline-grid", streaming && "motion-safe:animate-orb-breathe")} />
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <p className="sr-only">Assistant said:</p>
        {blocks}
        <Citations citations={merged} />
        {agent && !streaming && (
          <p className="text-xs text-muted">
            {AGENT_LABELS[agent]}
            {version ? `, version ${version}` : ""}
          </p>
        )}
      </div>
    </li>
  );
}
