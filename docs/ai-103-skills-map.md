# AI-103 skills map

How Benefura exercises the skills measured by **Microsoft Certified: Azure AI Apps and Agents
Developer Associate (exam AI-103)**, using the skills outline dated April 16, 2026. Each row points to
where the skill shows up in this repository.

| Skill area | What Benefura does | Where |
|---|---|---|
| Choose Foundry services and models | gpt-5-mini for extraction, verification and agents; gpt-5-nano for routing, triage and enrichment; text-embedding-3-small at 512 dimensions. Content Understanding layout instead of text reconstruction; push ingestion instead of an indexer; a search function tool instead of the built-in tool or Foundry IQ | `infra/modules/foundry.bicep`, [ADR 0002](adr/0002-browser-redaction-burned-into-images.md), [0004](adr/0004-knowledge-search-function-tool.md), [0005](adr/0005-push-model-ingestion.md) |
| Set up and CI/CD | Bicep for the AIServices account, project, deployments, guardrail and connections; azd; OIDC deploy; prompt agents published and version-pinned in CI | `infra/`, `azure.yaml`, `.github/workflows/deploy.yml`, `apps/api/app/agents/publish.py` |
| Manage, monitor and secure | TPM sizing, rate limits and dollar budgets; OpenTelemetry to App Insights, a workbook and alerts; index health; managed identity with local auth off and role assignments by GUID; optional private networking | `apps/api/app/{limits,budget,telemetry}.py`, `infra/modules/{monitoring,roles,network}.bicep`, [ADR 0008](adr/0008-cost-guardrails.md), [0011](adr/0011-private-networking-flag.md) |
| Responsible AI | Guardrail with jailbreak and indirect-attack blocking; Prompt Shields; image moderation; two-tier PII check; evaluators and red teaming; trace ids and provenance; approval flows, allowlists and step caps | `infra/modules/guardrail.bicep`, `apps/api/app/services/{prompt_shields,content_safety,language_pii}.py`, `evals/`, [transparency note](transparency-note.md), [threat model](threat-model.md) |
| Generative AI apps | RAG with citations and licences; an Extractor ⇄ Verifier workflow; cloud evaluations; Foundry SDKs; project connections | `apps/api/app/pipelines/booklet_workflow.py`, `apps/api/app/chat/server_tools.py`, `evals/cloud_evals.py` |
| Agents | Two prompt agents with roles, strict tool schemas and stateless conversation replay; retrieval plus function calling; agent-as-tool orchestration with a router; approvals; monitoring and error analysis | `apps/api/app/agents/`, `apps/api/app/chat/`, `apps/web/src/agent/`, [ADR 0003](adr/0003-agents-and-workflows.md), [0007](adr/0007-stateless-conversations.md) |
| Optimize and operate | Prompt and reasoning-effort tuning; a self-critique loop; token and latency analytics per step; multiple models combined with a deterministic rules engine | `apps/api/app/pipelines/prompts/`, `apps/web/src/domain/`, `infra/modules/monitoring.bicep` |
| Multimodal understanding | Content Understanding on redacted page images and receipts, including a custom field analyzer with confidence and source | `apps/api/app/services/content_understanding.py`, `apps/api/scripts/setup_cu.py` |
| Multimodal responsible AI | Image moderation for receipts; detecting prompt injection hidden in image text | `apps/api/app/pipelines/receipt.py`, `samples/receipts/injection-receipt.png` |
| Text analysis | Structured JSON extraction; PII and sensitive-content detection; a compliance-style plan summary with provenance | `apps/api/app/models/extraction.py`, `apps/api/app/services/language_pii.py`, `apps/web/src/app/review/` |
| Information extraction | Ingest and index; hybrid, vector and semantic search; enrichment; OCR ingestion; retrieval tool; CU layout markdown and custom analyzer | `knowledge/`, `apps/api/app/services/search.py` |

## Not covered, by choice

Image and video generation, speech, and translation. They don't serve the product: members read and
type about their own documents, and the demo runs in English for Canada and Australia. Adding French
(Translator) for Québec would be the natural next step.
