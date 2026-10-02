# 0003: Prompt agents for chat, Agent Framework workflows for extraction

**Status:** Accepted

## Context
Benefura has two very different AI workloads. Chat is interactive, multi-turn, uses tools that must
run in the browser, and benefits from versioning and portal traces. Booklet extraction is a
server-only batch pipeline with a verifier loop and structured outputs. Foundry workflows are
scheduled to retire on 2026-12-01, and an Agent Framework handoff cannot pause for a tool that runs
in the browser.

## Decision
- **Chat** uses two registered Foundry prompt agents, `benefura-plan-claims` and `benefura-knowledge`,
  called through the Responses API. CI publishes a new version only when the definition hash
  changes and pins the versions in the API's configuration.
- A gpt-5-nano **router** picks the agent; tool-output continuations stay with the active agent.
- **Agent-as-tool:** `ask_knowledge_agent` is a server function that makes a nested call to the
  knowledge agent (depth 1). A2A was rejected because it is text-only without streaming.
- **Extraction** is a Microsoft Agent Framework workflow: PageTriage (gpt-5-nano) → Extractor
  (gpt-5-mini) ⇄ Verifier (gpt-5-mini, at most 2 rounds) → deterministic grounding.

## Consequences
- Agent versions, traces and the Monitor dashboard are visible in the Foundry portal.
- Tool definitions are registered with an `executor` of `server` or `browser`; the API enforces a
  per-agent allowlist and a 6-step cap per user turn.
- The extraction workflow's orchestration is testable with a fake chat client.
