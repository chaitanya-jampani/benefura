// Replays the API's golden SSE files (apps/api/tests/chat/golden) through the AI SDK's own client pipeline.
import { readFileSync } from "node:fs";
import path from "node:path";

import { DefaultChatTransport, readUIMessageStream, type UIMessage } from "ai";
import { describe, expect, it } from "vitest";

import { shouldContinue } from "./continuation";
import type { BenefuraUIMessage } from "./types";

const GOLDEN = path.resolve(__dirname, "../../../api/tests/chat/golden");
const TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";

async function replayGolden(name: string): Promise<{ request: BenefuraUIMessage[]; message: BenefuraUIMessage }> {
  const body = readFileSync(path.join(GOLDEN, `${name}.sse`), "utf8");
  const request = JSON.parse(readFileSync(path.join(GOLDEN, `${name}.request.json`), "utf8")).messages as BenefuraUIMessage[];
  const transport = new DefaultChatTransport<BenefuraUIMessage>({
    api: "http://localhost:8765/api/chat",
    fetch: async () =>
      new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream", "x-vercel-ai-ui-message-stream": "v1", "x-benefura-trace-id": TRACE_ID },
      }),
  });
  const stream = await transport.sendMessages({
    chatId: "golden",
    messages: request,
    abortSignal: undefined,
    trigger: "submit-message",
    messageId: undefined,
  });
  const last = request.at(-1);
  const errors: unknown[] = [];
  let message: BenefuraUIMessage | undefined;
  for await (const snapshot of readUIMessageStream<BenefuraUIMessage>({
    message: last?.role === "assistant" ? structuredClone(last) : undefined,
    stream,
    onError: (e) => errors.push(e),
    terminateOnError: true,
  })) {
    message = snapshot;
  }
  expect(errors).toEqual([]);
  expect(message).toBeDefined();
  return { request, message: message! };
}

const types = (m: UIMessage) => m.parts.map((p) => p.type);

describe("golden UI message streams", () => {
  it("browser tool round: tool input arrives and the browser must continue", async () => {
    const { message } = await replayGolden("browser-tool-round");
    expect(message.id).toBe("msg_1");
    expect(message.metadata).toEqual({
      agent: "plan_claims",
      agentName: "benefura-plan-claims",
      agentVersion: "fake",
      traceId: TRACE_ID,
      route: "plan_claims",
    });
    expect(types(message)).toEqual(["step-start", "tool-find_benefits"]);
    expect(message.parts[1]).toMatchObject({ toolCallId: "call_pc_1", state: "input-available", input: { query: "massage" } });
    expect(shouldContinue({ messages: [message] })).toBe(false);

    const withOutput = structuredClone(message);
    Object.assign(withOutput.parts[1], { state: "output-available", output: { benefits: [] } });
    expect(shouldContinue({ messages: [withOutput] })).toBe(true);
  });

  it("approval request: the draft waits for the user", async () => {
    const { message } = await replayGolden("approval-request");
    const draft = message.parts.find((p) => p.type === "tool-draft_claim");
    expect(draft).toMatchObject({
      state: "approval-requested",
      approval: { id: "approval-call_pc_2" },
      input: { member_alias: "[MEMBER_A]", lines: [{ benefit_id: "ben-massage", charged_cents: 12000 }] },
    });
    expect(types(message)).toEqual(["step-start", "tool-find_benefits", "step-start", "tool-draft_claim"]);
    expect(shouldContinue({ messages: [message] })).toBe(false);

    const approved = structuredClone(message);
    Object.assign(approved.parts[3], { state: "approval-responded", approval: { id: "approval-call_pc_2", approved: true } });
    expect(shouldContinue({ messages: [approved] })).toBe(false); // must run in the browser first
  });

  it("declined approval: the part is denied and the assistant acknowledges", async () => {
    const { request, message } = await replayGolden("approval-declined");
    expect(shouldContinue({ messages: request })).toBe(true);
    const draft = message.parts.find((p) => p.type === "tool-draft_claim");
    expect(draft).toMatchObject({ state: "output-denied", approval: { approved: false, reason: "Wrong amount" } });
    const text = message.parts.findLast((p) => p.type === "text");
    expect(text).toMatchObject({ state: "done", text: "Okay, I won't create that claim. Tell me what you'd like to change." });
    expect(message.parts.filter((p) => p.type === "tool-draft_claim")).toHaveLength(1);
    expect(shouldContinue({ messages: [message] })).toBe(false);
  });

  it("mixed question: status updates in place, citations and both tool kinds", async () => {
    const { message } = await replayGolden("mixed-status-citations");
    const statuses = message.parts.filter((p) => p.type === "data-status");
    expect(statuses).toEqual([
      { type: "data-status", id: "status-call_pc_1", data: { message: "Checked public reference material", state: "done" } },
    ]);
    const citations = message.parts.find((p) => p.type === "data-citations");
    expect(citations?.type === "data-citations" && citations.data.citations.map((c) => c.publisher)).toEqual([
      "Canada Revenue Agency",
      "Canada Revenue Agency",
    ]);
    expect(message.parts.find((p) => p.type === "tool-ask_knowledge_agent")).toMatchObject({
      state: "output-available",
      providerExecuted: true,
      output: { nestedToolCalls: 1 },
    });
    expect(message.parts.find((p) => p.type === "tool-find_benefits")).toMatchObject({ state: "input-available" });
  });

  it("knowledge answer: search output, citations with licence and a finished text part", async () => {
    const { message } = await replayGolden("knowledge-citations");
    expect(message.metadata?.agent).toBe("knowledge");
    const citations = message.parts.find((p) => p.type === "data-citations");
    const list = citations?.type === "data-citations" ? citations.data.citations : [];
    expect(list.length).toBeGreaterThan(0);
    for (const citation of list) {
      expect(citation).toMatchObject({ license: expect.any(String), attribution: expect.any(String), url: expect.stringMatching(/^https:\/\//) });
    }
    const text = message.parts.findLast((p) => p.type === "text");
    expect(text).toMatchObject({ state: "done", text: expect.stringContaining("Canada Revenue Agency") });
  });

  it("hard-tier PII: a warning part and a blocked turn", async () => {
    const { message } = await replayGolden("pii-warning");
    expect(message.metadata).toMatchObject({ blocked: "pii", agent: null });
    expect(message.parts).toEqual([
      { type: "data-pii-warning", data: { categories: ["CASocialInsuranceNumber"], message: expect.stringContaining("wasn't sent") } },
    ]);
  });

  it("off topic: a canned refusal", async () => {
    const { message } = await replayGolden("off-topic");
    expect(message.metadata?.route).toBe("off_topic");
    expect(message.parts).toEqual([{ type: "text", text: expect.stringContaining("I can only help with health benefits"), state: "done" }]);
  });

  it("content filter: a refusal and a blocked turn", async () => {
    const { message } = await replayGolden("content-filter");
    expect(message.metadata?.blocked).toBe("content_filter");
    expect(message.parts.find((p) => p.type === "text")).toMatchObject({ text: expect.stringContaining("I can't help with that request") });
  });
});
