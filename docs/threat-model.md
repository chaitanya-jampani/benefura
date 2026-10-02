# Threat model

Scope: the public demo (static site, API, Foundry project, Search, Storage) and the browser-side
redaction flow. Method: data-flow review with STRIDE per trust boundary.

## Assets

1. Personal information in documents a visitor chooses to upload (names, identifiers, health details).
2. The alias map and plans stored in the visitor's browser.
3. The Azure subscription's spend (model tokens, Content Understanding pages, Language and Content Safety records).
4. Integrity of answers: plan numbers, citations and claim drafts.
5. The availability of the public demo.

## Trust boundaries

| Boundary | Crosses it |
|---|---|
| Device → API | Redacted images, aliased chat text, tool outputs |
| API → Foundry / Azure AI services | Same content, with a managed identity |
| Public web → knowledge index | Third-party public pages, fetched weekly |
| GitHub Actions → Azure | OIDC federated credential, no stored secrets |

## Threats and mitigations

| # | Threat (STRIDE) | Mitigation | Residual risk |
|---|---|---|---|
| T1 | Raw PII leaves the device (I) | Redaction burned into pixels; image-only PDFs without metadata; acknowledgment on the exact images; Playwright tests assert no text layer, solid pixels under boxes and no fake PII in any request | Detector misses; user acknowledges without reviewing |
| T2 | Missed identifier reaches Azure (I) | Server two-tier Language PII check rejects hard-tier hits with the page and polygon so the user can add a box | Backstop only: the image has already been processed (documented in [privacy](privacy.md)) |
| T3 | Prompt injection inside a booklet or receipt (T, E) | Prompt Shields on all CU text; flagged pages excluded; guardrail blocks indirect attacks; extraction output is structured JSON validated by schema and grounded against the page text | Novel injections that pass both shields affect only structured rows the user reviews |
| T4 | Prompt injection via knowledge sources (T) | Tool results wrapped in `<documents>` so the guardrail's indirect-attack filter applies (M0 spike 10 verifies; otherwise Prompt Shields runs on results); curated source list; licence filter | Compromised public page between weekly fetches |
| T5 | Jailbreak / off-topic abuse of the chat (E, D) | Router refuses off-topic without a large-model call; guardrail blocks jailbreaks; per-agent tool allowlists; ≤6 tool steps; nesting depth 1; content-filter errors map to a refusal | Low-value misuse within budget |
| T6 | Agent changes data without consent (T) | `draft_claim` and `update_claim` require an approval card; declined approvals return `{declined: true}` and must not be retried; claim history records agent name, version and trace id | — |
| T7 | Cost exhaustion (D) | Per-IP rate limit; ≤120 pages per booklet; ≤3 booklets per IP per day; daily dollar budgets per namespace (extraction, chat, evals); resource-group budget alert; 0–1 replicas | Distributed abuse can spend the daily budget, which pauses AI features until UTC midnight; the dashboard keeps working |
| T8 | Exfiltration from the browser via injected script (I) | CSP `connect-src 'self' <api>`, `form-action 'self'`, `object-src 'none'`, `frame-ancestors 'none'`; safe markdown without images or raw HTML; no third-party scripts | `'unsafe-inline'` scripts are required by the static export |
| T9 | Credential theft (S, E) | Local auth disabled on Foundry, Search and Storage; managed identity; OIDC in CI; least-privilege role assignments | Compromise of the GitHub repo's deploy workflow |
| T10 | Sensitive data in logs (I) | Request bodies never logged; content recording off; metrics use categories and counts | Foundry server-side agent traces contain aliased messages (disclosed) |
| T11 | Spoofed client IP to evade limits (S) | IP from the first `X-Forwarded-For` hop set by Container Apps ingress; budgets cap total spend regardless | Rotating IPs |
| T12 | Eval traffic locks out the demo (D) | Evals use a separate budget namespace selected only with a shared secret | — |

## Out of scope

Real claim submission to insurers, account systems, and multi-user data. The optional
`enablePrivateNetworking` Bicep flag shows how Foundry and Storage move behind private endpoints for
production; AI Search Free and Static Web Apps Free do not support it.
