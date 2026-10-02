# 0001: Agent Framework dependency set

**Status:** Accepted (M0 spike 14, 2026-09-16)

## Context
The plan pins `agent-framework` 1.18, `agent-framework-foundry` 1.13, `azure-ai-projects` <2.7 and
`azure-ai-contentunderstanding` 1.1. A clean `uv lock` failed: the `agent-framework` meta-package
depends on `agent-framework-core[all]`, which pulls the Content Understanding connector, which
requires `azure-ai-contentunderstanding>=1.0.1,<1.1` (or a 1.2 beta).

## Decision
Depend on `agent-framework-core==1.18.0` and `agent-framework-foundry==1.13.0` directly. Benefura
calls Content Understanding through its own service module, so it doesn't need the connector.

## Result
Resolves with `azure-ai-projects` 2.6.1, `azure-ai-contentunderstanding` 1.1.0,
`agent-framework-openai` 1.14.3 and `openai` 3.14.1. `uv.lock` is committed.

## Consequences
Additional Agent Framework connectors must be added one at a time and checked for pin conflicts.
