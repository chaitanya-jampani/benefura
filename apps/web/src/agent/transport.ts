import { DefaultChatTransport, getToolName, isToolUIPart } from "ai";

import { chatFetch, chatUrl } from "@/api/client";
import { db } from "@/db/dexie";
import { todayIso } from "@/domain/periods";
import type { ChatRequest } from "@/domain/types";

import { loadActivePlanRecord } from "./activePlan";
import { applyAliases, buildChatContext } from "./context";
import type { AgentKey, BenefuraUIMessage } from "./types";

// ChatRequest allows 80 messages; keep a margin.
export const MAX_SENT_MESSAGES = 60;

export function lastAgent(messages: BenefuraUIMessage[]): AgentKey | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "user") return null;
    if (m.role === "assistant" && m.metadata?.agent) return m.metadata.agent;
  }
  return null;
}

export function outgoingMessages(
  messages: BenefuraUIMessage[],
  aliases: { token: string; values: string[] }[],
): BenefuraUIMessage[] {
  let start = Math.max(0, messages.length - MAX_SENT_MESSAGES);
  while (start > 0 && start < messages.length && messages[start].role !== "user") start++;
  return messages.slice(start).map((message) => ({
    ...message,
    parts: message.parts
      .filter((part) => !part.type.startsWith("data-"))
      .map((part) => (message.role === "user" && part.type === "text" ? { ...part, text: applyAliases(part.text, aliases) } : part)),
  }));
}

export function previewOfChatBody(body: string): string {
  try {
    const request = JSON.parse(body) as Omit<ChatRequest, "messages"> & { messages: BenefuraUIMessage[] };
    const last = request.messages.at(-1);
    if (!last) return "";
    if (last.role === "user") {
      return last.parts.map((p) => (p.type === "text" ? p.text : "")).join("\n");
    }
    const stepStart = last.parts.findLastIndex((p) => p.type === "step-start");
    const results = last.parts
      .slice(stepStart + 1)
      .filter(isToolUIPart)
      .filter((p) => !p.providerExecuted)
      .map((p) => {
        const name = getToolName(p);
        if (p.state === "output-available") return `${name} result:\n${JSON.stringify(p.output, null, 2)}`;
        if (p.state === "output-error") return `${name} error: ${p.errorText}`;
        if (p.state === "output-denied" || (p.state === "approval-responded" && p.approval.approved === false)) {
          return `${name}: declined${p.approval.reason ? ` (${p.approval.reason})` : ""}`;
        }
        return `${name}: ${p.state}`;
      });
    return results.length ? results.join("\n\n") : "Continue the conversation";
  } catch {
    return "";
  }
}

export function createChatTransport(): DefaultChatTransport<BenefuraUIMessage> {
  return new DefaultChatTransport<BenefuraUIMessage>({
    fetch: chatFetch(previewOfChatBody),
    prepareSendMessagesRequest: async ({ messages }) => {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      const [api, active, aliases] = await Promise.all([chatUrl(), loadActivePlanRecord(), db.aliases.toArray()]);
      const body: ChatRequest = {
        messages: outgoingMessages(messages, aliases) as unknown as ChatRequest["messages"],
        context: buildChatContext({ plan: active?.plan ?? null, today: todayIso(tz), tz }),
        activeAgent: lastAgent(messages),
      };
      return { api, body };
    },
  });
}
