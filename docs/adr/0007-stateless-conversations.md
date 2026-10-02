# 0007: Stateless conversations

**Status:** Accepted. Replay behaviour pending M0 spike 1.

## Context
Storing conversations in Foundry would put member conversations (even aliased) in a server-side
store with its own retention, and would make the browser-held history and the server history drift.

## Decision
Every Responses API call uses `store=false`. The browser keeps the full UI message history in
IndexedDB and sends it each turn. When replaying: strip item ids, drop reasoning items, and set
`parallel_tool_calls=false` so tool calls and outputs pair up deterministically.

## Consequences
Slightly larger requests (bounded to 80 messages) and no server-side conversation features.
Continuous evaluation rules that depend on stored responses may not work ([ADR 0010](0010-evaluation-strategy.md)).
