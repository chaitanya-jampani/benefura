// Spike 2 (client half): drives the UI message stream with the AI SDK transport and reader, without React.
// Like useChat, tool outputs and approvals are added to the same assistant message and the conversation re-sent.

import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const api = process.argv[2] ?? "http://localhost:8799/api/chat";
const require = createRequire(path.join(process.cwd(), "package.json"));
const ai = await import(pathToFileURL(require.resolve("ai")).href);
const { DefaultChatTransport, readUIMessageStream } = ai;
if (!DefaultChatTransport || !readUIMessageStream) {
  console.error("FAIL: ai does not export DefaultChatTransport/readUIMessageStream; check the v7 API.");
  process.exit(1);
}

const transport = new DefaultChatTransport({ api });
const user = { id: "u1", role: "user", parts: [{ type: "text", text: "How much massage do I have left?" }] };
let assistant;

async function round(label) {
  const stream = await transport.sendMessages({
    chatId: "spike-s02",
    messages: assistant ? [user, assistant] : [user],
    abortSignal: undefined,
    trigger: "submit-message",
    messageId: undefined,
  });
  for await (const message of readUIMessageStream({ message: assistant, stream })) assistant = message;
  console.log(`${label}:`, JSON.stringify(assistant?.parts?.map((p) => ({ type: p.type, state: p.state }))));
}

const replacePart = (predicate, update) => {
  assistant = { ...assistant, parts: assistant.parts.map((p) => (predicate(p) ? { ...p, ...update(p) } : p)) };
};

const checks = {};

await round("round 1");
checks["browser tool input available"] = assistant?.parts?.some(
  (p) => p.type === "tool-get_usage" && p.state === "input-available",
);
replacePart(
  (p) => p.type === "tool-get_usage",
  () => ({ state: "output-available", output: { remainingCents: 26000 } }),
);

await round("round 2");
const draft = assistant?.parts?.find((p) => p.type === "tool-draft_claim");
checks["approval requested"] = draft?.state === "approval-requested" && Boolean(draft?.approval?.id);
replacePart(
  (p) => p.type === "tool-draft_claim",
  (p) => ({ state: "approval-responded", approval: { ...p.approval, approved: true } }),
);

await round("round 3");
checks["approved tool output + final text"] =
  assistant?.parts?.some((p) => p.type === "tool-draft_claim" && p.state === "output-available") &&
  assistant?.parts?.filter((p) => p.type === "text").length >= 2;

for (const [name, ok] of Object.entries(checks)) console.log(`[${ok ? "ok  " : "FAIL"}] ${name}`);
const passed = Object.values(checks).every(Boolean);
console.log(passed ? "PASS: s02" : "FAIL: s02");
process.exit(passed ? 0 : 1);
