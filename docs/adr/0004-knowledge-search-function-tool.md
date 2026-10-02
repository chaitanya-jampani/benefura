# 0004: Knowledge retrieval as a server function tool

**Status:** Accepted. Foundry IQ knowledge-base MCP tool pending M0 spike 7.

## Context
Options for grounding the knowledge agent on public CA/AU content:
1. The built-in Azure AI Search agent tool.
2. A Foundry IQ knowledge base exposed as an MCP tool.
3. A server-executed function tool that queries AI Search in code.

The built-in tool doesn't support gpt-5-mini. Benefura also needs hybrid + vector + semantic
ranking, `store=false`, and licence/attribution metadata on every citation.

## Decision
`search_public_knowledge(query, region, top_k)` runs in the API: hybrid BM25 + vector
(text-embedding-3-small, 512 dimensions) with the semantic ranker, filtered by region. Results are
returned to the model wrapped in `<documents><document source= title= license=>` so the guardrail's
indirect-attack detection applies, and a `data-citations` stream part drives the citation UI.

## Consequences
Full control of query shape, telemetry (latency, zero-result rate, score distribution) and
citations. If spike 7 shows Foundry IQ works with gpt-5-mini and `store=false`, it can replace the
function without changing the browser.
