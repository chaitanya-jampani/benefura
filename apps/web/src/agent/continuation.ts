import { isToolUIPart, type UIMessage } from "ai";

import type { BenefuraUIMessage } from "./types";

// Continue once every browser tool call in the last step has an output, an error or a declined approval.
// An approved call is incomplete until the browser has run it, and blocked turns never continue.
export function shouldContinue({ messages }: { messages: UIMessage[] }): boolean {
  const last = messages.at(-1) as BenefuraUIMessage | undefined;
  if (!last || last.role !== "assistant" || last.metadata?.blocked) return false;
  const stepStart = last.parts.findLastIndex((p) => p.type === "step-start");
  const calls = last.parts.slice(stepStart + 1).filter(isToolUIPart).filter((p) => !p.providerExecuted);
  if (calls.length === 0) return false;
  return calls.every(
    (p) =>
      p.state === "output-available" ||
      p.state === "output-error" ||
      p.state === "output-denied" ||
      (p.state === "approval-responded" && p.approval.approved === false),
  );
}
