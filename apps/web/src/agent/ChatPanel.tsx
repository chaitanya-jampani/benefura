"use client";

import { useChat } from "@ai-sdk/react";
import { getToolName, isToolUIPart } from "ai";
import { useLiveQuery } from "dexie-react-hooks";
import { SquarePen } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useHealth } from "@/api/useHealth";
import { Composer, type ComposerHandle } from "@/components/chat/Composer";
import { MessageView } from "@/components/chat/MessageView";
import { describeChatError, ErrorNotice } from "@/components/chat/Notices";
import type { AnyToolPart } from "@/components/chat/ToolCard";
import { Button, Orb, Surface } from "@/components/ui";
import { db, type ChatRecord } from "@/db/dexie";
import type { Plan, Region } from "@/domain/types";

import { loadActivePlanRecord } from "./activePlan";
import { applyAliases } from "./context";
import { shouldContinue } from "./continuation";
import { APPROVAL_TOOLS, isBrowserTool, runBrowserTool, ToolError, type AgentProvenance } from "./tools";
import { createChatTransport, lastAgent } from "./transport";
import type { BenefuraUIMessage, BrowserToolName } from "./types";

const SUGGESTIONS: Record<Region, string[]> = {
  CA: [
    "How much massage do I have left?",
    "Is massage covered and is it a medical expense for tax?",
    "Draft a claim for a $120 massage today",
    "What does my plan cover for glasses?",
  ],
  AU: [
    "How much physio do I have left?",
    "Is remedial massage covered and does it count for the Medicare levy surcharge?",
    "Draft a claim for a $90 physio visit today",
    "What are the waiting periods for hospital cover?",
  ],
};

function newChatRecord(planId: string | undefined): ChatRecord {
  const now = new Date().toISOString();
  return { id: crypto.randomUUID(), planId, messages: [], createdAt: now, updatedAt: now };
}

async function loadLatestChat(): Promise<ChatRecord> {
  const active = await loadActivePlanRecord();
  const chats = active
    ? await db.chats.where("planId").equals(active.id).sortBy("updatedAt")
    : (await db.chats.orderBy("updatedAt").toArray()).filter((c) => !c.planId);
  return chats.at(-1) ?? newChatRecord(active?.id);
}

export function ChatPanel() {
  const [record, setRecord] = useState<ChatRecord | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadLatestChat().then((chat) => {
      if (!cancelled) setRecord(chat);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!record) {
    return (
      <Surface glow className="min-h-[24rem]" aria-busy="true">
        <p className="text-muted">Opening your conversation…</p>
      </Surface>
    );
  }
  return <ChatSession key={record.id} record={record} onNewChat={() => setRecord(newChatRecord(record.planId))} />;
}

function textOf(message: BenefuraUIMessage | undefined): string {
  return message?.parts.map((p) => (p.type === "text" ? p.text : "")).join("\n") ?? "";
}

function provenanceOf(message: BenefuraUIMessage): AgentProvenance {
  return {
    agentName: message.metadata?.agentName ?? null,
    agentVersion: message.metadata?.agentVersion ?? null,
    traceId: message.metadata?.traceId ?? null,
  };
}

function ChatSession({ record, onNewChat }: { record: ChatRecord; onNewChat: () => void }) {
  const plan = useLiveQuery(async () => (await loadActivePlanRecord())?.plan ?? null, [], undefined as Plan | null | undefined);
  const { state: health } = useHealth();
  // Prefill ?q= rather than send it, so the user decides.
  const [draft, setDraft] = useState(() => new URLSearchParams(window.location.search).get("q")?.slice(0, 500) ?? "");
  const [busyToolCallId, setBusyToolCallId] = useState<string | null>(null);
  const composerRef = useRef<ComposerHandle>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const transport = useMemo(() => createChatTransport(), []);

  useEffect(() => {
    if (window.location.search) window.history.replaceState(null, "", window.location.pathname);
  }, []);

  const chat = useChat<BenefuraUIMessage>({
    id: record.id,
    messages: record.messages as BenefuraUIMessage[],
    transport,
    sendAutomaticallyWhen: shouldContinue,
    onToolCall: ({ toolCall }) => {
      if (toolCall.dynamic || !isBrowserTool(toolCall.toolName) || APPROVAL_TOOLS.has(toolCall.toolName)) return;
      // Not awaited: the stream keeps flowing and the SDK sends the results once every call has one.
      void runAndReport(toolCall.toolName, toolCall.toolCallId, toolCall.input);
    },
  });
  const { messages, status, error } = chat;
  const streaming = status === "streaming" || status === "submitted";

  const runAndReport = useCallback(
    async (name: BrowserToolName, toolCallId: string, input: unknown, provenance?: AgentProvenance) => {
      try {
        const output = await runBrowserTool(name, input, { provenance });
        await chat.addToolOutput({ tool: name, toolCallId, output });
      } catch (err) {
        const errorText = err instanceof ToolError || err instanceof Error ? err.message : "The tool failed.";
        await chat.addToolOutput({ tool: name, toolCallId, state: "output-error", errorText });
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `chat` is stable and its methods read the latest state
    [],
  );

  useEffect(() => {
    if (streaming || messages.length === 0) return;
    void db.chats.put({
      ...record,
      messages,
      activeAgent: lastAgent(messages) ?? undefined,
      updatedAt: new Date().toISOString(),
    });
  }, [messages, streaming, record]);

  // Finish work interrupted by a reload.
  const resumed = useRef(false);
  useEffect(() => {
    if (resumed.current) return;
    resumed.current = true;
    const last = messages.at(-1);
    if (!last || last.role !== "assistant") return;
    const pending = last.parts
      .filter(isToolUIPart)
      .filter((p) => !p.providerExecuted && p.state === "input-available")
      .filter((p) => isBrowserTool(getToolName(p)) && !APPROVAL_TOOLS.has(getToolName(p)));
    if (pending.length > 0) {
      pending.forEach((p) => void runAndReport(getToolName(p) as BrowserToolName, p.toolCallId, p.input));
    } else if (shouldContinue({ messages })) {
      void chat.sendMessage();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once for the restored conversation
  }, []);

  const lastPartCount = messages.at(-1)?.parts.length ?? 0;
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ block: "end", behavior: reduced ? "auto" : "smooth" });
  }, [messages.length, lastPartCount, status]);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    const aliases = await db.aliases.toArray();
    setDraft("");
    await chat.sendMessage({ text: applyAliases(trimmed, aliases) });
  };

  const approve = async (message: BenefuraUIMessage, part: AnyToolPart) => {
    if (part.state !== "approval-requested" || busyToolCallId) return;
    const name = getToolName(part) as BrowserToolName;
    setBusyToolCallId(part.toolCallId);
    try {
      let output: unknown;
      let errorText: string | null = null;
      try {
        output = await runBrowserTool(name, part.input, { provenance: provenanceOf(message) });
      } catch (err) {
        errorText = err instanceof Error ? err.message : "The change could not be saved.";
      }
      await chat.addToolApprovalResponse({ id: part.approval.id, approved: true });
      if (errorText === null) {
        await chat.addToolOutput({ tool: name, toolCallId: part.toolCallId, output });
      } else {
        await chat.addToolOutput({ tool: name, toolCallId: part.toolCallId, state: "output-error", errorText });
      }
    } finally {
      setBusyToolCallId(null);
    }
  };

  const decline = (part: AnyToolPart, reason: string) => {
    if (part.state !== "approval-requested") return;
    void chat.addToolApprovalResponse({ id: part.approval.id, approved: false, reason: reason || undefined });
  };

  const editBlocked = (assistantMessageId: string) => {
    const index = messages.findIndex((m) => m.id === assistantMessageId);
    if (index < 1) return;
    setDraft(textOf(messages[index - 1]));
    chat.setMessages(messages.filter((_, i) => i !== index && i !== index - 1));
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const retry = () => {
    chat.clearError();
    const last = messages.at(-1);
    if (last?.role === "user") void chat.regenerate();
    else void chat.sendMessage();
  };

  const aiUnavailable = health.status === "ai_off" || health.status === "budget_exhausted";
  const disabledReason =
    health.status === "ai_off"
      ? "AI features are off for this demo"
      : health.status === "budget_exhausted"
        ? "Today's AI budget is used up"
        : undefined;
  const region: Region = plan?.region ?? "CA";
  const liveStatus = status === "submitted" ? "Sending" : status === "streaming" ? "The assistant is answering" : "";

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-4">
          <Orb size={48} className="motion-safe:animate-orb-breathe" />
          <div className="min-w-0">
            <h1 className="text-3xl font-medium tracking-tight text-ink sm:text-4xl">Ask Benefura</h1>
            <p className="text-base text-muted sm:text-lg">Answers from your plan, your claims and public reference material.</p>
          </div>
        </div>
        {messages.length > 0 && (
          <Button variant="ghost" size="sm" onClick={onNewChat} disabled={streaming} aria-label="Start a new chat">
            <SquarePen aria-hidden className="size-4" strokeWidth={1.5} />
            <span className="hidden sm:inline">New chat</span>
          </Button>
        )}
      </header>

      <Surface glow className="flex min-h-[22rem] flex-col gap-6 px-4 py-6 sm:p-10" aria-label="Conversation">
        <p aria-live="polite" className="sr-only">
          {liveStatus}
        </p>
        {messages.length === 0 ? (
          <div className="flex flex-1 flex-col justify-center gap-6">
            <p className="max-w-lg text-[clamp(1.75rem,5vw,2.75rem)] leading-tight font-light tracking-tight text-ink">
              What would you like to know about your benefits?
            </p>
            {plan === null && (
              <p className="text-muted">No plan is loaded yet, so plan questions won&apos;t have much to work with. Public reference questions still work.</p>
            )}
            <ul className="flex flex-wrap gap-3" aria-label="Suggested questions">
              {SUGGESTIONS[region].map((prompt) => (
                <li key={prompt}>
                  <button
                    type="button"
                    disabled={aiUnavailable}
                    onClick={() => void send(prompt)}
                    className="pill-soft rounded-full px-5 py-3 text-left text-base text-ink transition-transform active:scale-[0.98] disabled:opacity-40"
                  >
                    {prompt}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <ol className="flex flex-col gap-8" data-testid="chat-messages">
            {messages.map((message, i) => {
              const next = messages[i + 1];
              const isLast = i === messages.length - 1;
              return (
                <MessageView
                  key={message.id}
                  message={message}
                  plan={plan ?? null}
                  notSent={message.role === "user" && next?.metadata?.blocked === "pii"}
                  interactive={isLast && !streaming}
                  streaming={isLast && streaming && message.role === "assistant"}
                  busyToolCallId={busyToolCallId}
                  onApprove={(m, part) => void approve(m, part)}
                  onDecline={decline}
                  onEditBlocked={editBlocked}
                />
              );
            })}
          </ol>
        )}
        {status === "submitted" && messages.at(-1)?.role === "user" && (
          <p className="flex items-center gap-3 text-muted" data-testid="chat-thinking">
            <Orb size={24} className="shadow-none motion-safe:animate-orb-breathe" />
            Thinking…
          </p>
        )}
        {error && <ErrorNotice message={describeChatError(error)} onRetry={retry} onDismiss={chat.clearError} />}
        <div ref={endRef} />
      </Surface>

      <div className="sticky bottom-[calc(7rem+env(safe-area-inset-bottom))] z-30 -mx-4 flex flex-col gap-2 bg-linear-to-t from-canvas via-canvas/95 to-canvas/0 px-4 pt-6 pb-2 sm:-mx-8 sm:px-8">
        <Composer
          ref={composerRef}
          value={draft}
          onChange={setDraft}
          onSend={() => void send(draft)}
          onStop={() => void chat.stop()}
          streaming={streaming}
          disabled={aiUnavailable}
          disabledReason={disabledReason}
        />
        <p className="px-5 text-center text-xs text-muted sm:text-sm">
          Names you&apos;ve hidden are swapped for aliases before sending. Not insurance or tax advice.
        </p>
      </div>
    </div>
  );
}
