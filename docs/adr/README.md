# Architecture decision records

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-agent-framework-dependencies.md) | Depend on `agent-framework-core` + `agent-framework-foundry`, not the meta-package | Accepted (M0 spike 14) |
| [0002](0002-browser-redaction-burned-into-images.md) | Redact in the browser and send only rasterized images | Accepted |
| [0003](0003-agents-and-workflows.md) | Prompt agents for chat, Agent Framework workflows for extraction | Accepted |
| [0004](0004-knowledge-search-function-tool.md) | Knowledge retrieval as a server function tool over AI Search | Accepted; Foundry IQ pending M0 spike 7 |
| [0005](0005-push-model-ingestion.md) | Push-model ingestion instead of an indexer and skillset | Accepted |
| [0006](0006-regions-and-data-residency.md) | East US 2 for the demo, AI Search in Canada Central; in-country for production | Accepted; pending M0 spikes 8–9 |
| [0007](0007-stateless-conversations.md) | `store=false` with history replayed from the browser | Accepted; pending M0 spike 1 |
| [0008](0008-cost-guardrails.md) | Dollar budgets per namespace, S0 Language/Content Safety, GHCR | Accepted |
| [0009](0009-two-tier-pii-policy.md) | Hard-tier PII blocks, advisory-tier PII reports | Accepted; calibrated by M0 spike 11 |
| [0010](0010-evaluation-strategy.md) | Scheduled cloud evaluations; continuous evaluation only if it works statelessly | Proposed; pending M0 spike 7 |
| [0011](0011-private-networking-flag.md) | Private networking as an off-by-default Bicep flag | Accepted |

Spike procedures and commands are in [`spikes/README.md`](../../spikes/README.md). When a spike
changes a decision, update the ADR's status and "Result" section in the same pull request.
