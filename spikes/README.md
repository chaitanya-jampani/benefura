# M0 spikes: runbook

Each spike answers one question the plan cannot settle on paper. Results are written to `spikes/results/*.json`, which
is git-ignored. The conclusion goes into the ADR named in each section (`docs/adr/NNNN-<slug>.md`; the number is
assigned when the ADR is written). Spikes are throwaway code: they use the pinned SDKs from `apps/api`, but nothing in
the app imports them.

## Common setup

```sh
az login                                    # or: azd auth login (DefaultAzureCredential uses either)
azd env new benefura-m0 --location eastus2  # a scratch environment for spikes
azd env set API_IMAGE ghcr.io/<owner>/benefura-api:latest
azd provision                               # Foundry, deployments, guardrail, search, storage, monitoring, ACA
azd env get-values > apps/api/.env          # spikes read endpoints + deployment names through app.config
set -a && . apps/api/.env && set +a         # for the shell spikes
cd apps/api && uv sync                      # Python spikes run inside the API environment
```

How to run each kind of spike:
- Python: `cd apps/api && uv run python ../../spikes/<script>.py`
- Shell: from the repo root, `bash spikes/<script>.sh`
- Node: `cd apps/web && node ../../spikes/<script>.mjs`

Spikes 8 and 9 need no provisioning. Run them first: they decide the model capacity and the search parameters for
`azd provision`.

| # | Spike | Script | Needs provisioning | ADR |
|---|---|---|---|---|
| 1 | Responses API `store=False` replay, `tool_choice`, agent-as-tool | `s01_responses_store_false.py` | yes | stateless-responses-and-prompt-agents |
| 2 | `useChat` 7.x ↔ FastAPI UI message stream | `s02_ui_stream_server.py` + `s02_ui_stream_client.mjs` | no | chat-streaming-protocol |
| 3 | Content Understanding layout at 200/300 DPI, defaults, receipt analyzer, result deletion | `s03_cu_layout.py` | yes | content-understanding-and-page-rasterization |
| 4 | Container Apps ingress: 8 MB body, 240 s | `s04_aca_ingress.sh` | yes | container-apps-consumption-limits |
| 5 | Agent Framework Extractor ⇄ Verifier workflow, traced without content | `s05_agent_framework_workflow.py` | yes | agent-framework-extraction-workflow |
| 6 | Language PII + Prompt Shields keyless | `s06_language_pii_prompt_shields.py` | yes | pii-two-tier-policy-and-prompt-shields |
| 7 | Foundry IQ MCP tool; continuous evaluation with `store=False` | `s07_foundry_iq_and_continuous_eval.py` | yes (+ index) | knowledge-retrieval-tool-choice, continuous-evaluation |
| 8 | Quota for the three deployments (≥200K TPM chat) | `s08_quota.sh` | no | model-deployments-and-throughput |
| 9 | AI Search Free: existing service, Canada Central, RBAC, semantic free | `s09_search_free.sh` | no | ai-search-region-and-tier |
| 10 | Indirect-attack guardrail on tool outputs | `s10_indirect_attack_guardrail.py` | yes | stateless-responses-and-prompt-agents (guardrail) |
| 11 | PII false-positive calibration | `s11_pii_calibration.py` | yes | pii-two-tier-policy-and-prompt-shields |
| 12 | CSP against the real static export on SWA | `s12_csp_check.mjs` | yes (web deployed) | csp-for-static-export |
| 13 | Throughput: 60 pages at concurrency 4 | `s13_throughput.py` | yes (API deployed) | container-apps-consumption-limits, model-deployments-and-throughput |
| 14 | Dependency trio resolves | (done) | no | dependency-pins |

---

## 1. Responses API with `store=False`, tool replay, `tool_choice`, agent-as-tool

- **Goal:** prove the chat design works statelessly on gpt-5-mini:
  - Strict function tools.
  - 3 user turns × up to 2 tool rounds, with the client replaying history (item ids stripped, reasoning items dropped).
  - `tool_choice="none"` after the step cap.
  - A nested `ask_knowledge_agent` call.
  - The same loop through a registered prompt agent (`agent_reference`).
- **Prerequisites:** provisioned environment; your identity has Foundry User on the account (`roles.bicep` grants it to
  the azd principal).
- **Command:**
  ```sh
  cd apps/api && uv run python ../../spikes/s01_responses_store_false.py --prompt-agent
  uv run python ../../spikes/s01_responses_store_false.py --keep-reasoning   # compare replay with encrypted reasoning
  ```
- **Pass:**
  - All 3 turns complete with no replay error.
  - At least one tool round and one nested call.
  - `tool_choice=none` yields no function call.
  - The prompt agent loop completes with `store=False`.
  - Also record whether dropping reasoning items changes the answers compared with `--keep-reasoning`.
- **ADR:** `docs/adr/NNNN-stateless-responses-and-prompt-agents.md`.

## 2. `useChat` 7.x ↔ FastAPI (browser-tool round + approval round)

- **Goal:** confirm the Python SSE encoder speaks AI SDK UI message stream v1 closely enough for `useChat`. That means
  `tool-input-available` ends a request for a browser tool, and `tool-approval-request` plus an `approval-responded`
  part drive the approval card.
- **Prerequisites:** none (scripted server, no model calls). Run `pnpm install` in the repo.
- **Command:**
  ```sh
  cd apps/api && uv run uvicorn --app-dir ../../spikes s02_ui_stream_server:app --port 8799
  cd apps/web && node ../../spikes/s02_ui_stream_client.mjs http://localhost:8799/api/chat
  ```
  Then point the web chat panel at `http://localhost:8799` (`NEXT_PUBLIC_API_BASE_URL`) and click through one tool round
  and one approval in the browser.
- **Pass:**
  - The client prints `PASS: s02`: the parts reach `input-available`, then `approval-requested`, then `output-available`.
  - `useChat` in the browser renders the tool card and the approval card and re-sends automatically after approval.
- **Status (2026-09-16):** the protocol half **passed** locally against `ai` 7.0.105 with the scripted server
  (`DefaultChatTransport` + `readUIMessageStream`). The browser (`useChat`) half is still open.
- **ADR:** `docs/adr/NNNN-chat-streaming-protocol.md`.

## 3. Content Understanding `prebuilt-layout` on redacted image-only chunks

- **Goal:** for 5-page image-only PDFs at 200 and 300 DPI, measure:
  - Analyze time.
  - PDF size against the 8 MB cap.
  - Markdown tokens per page.
  - Digit-OCR accuracy against the golden page markdown.

  Also exercise the defaults PATCH, a throwaway custom receipt analyzer, and `delete_result` after every read.
- **Prerequisites:** provisioned environment; `brew install poppler` (for `pdftoppm`); the sample booklet and golden
  pages from `samples/`. Burn in real redactions from the web app for the final numbers.
- **Command:**
  ```sh
  cd apps/api && uv run python ../../spikes/s03_cu_layout.py \
    --pdf ../../samples/ca-northwind-booklet.pdf --expected ../../samples/golden/ca-northwind.pages.json \
    --update-defaults --receipt ../../samples/receipts/<receipt>.jpg
  ```
- **Pass:**
  - Each chunk is ≤ 8 MB.
  - Analyze takes < 60 s per chunk.
  - Minimum digit accuracy ≥ 0.995 at the chosen DPI. The script records `recommendedDpi`, the lowest DPI that meets it.
  - The defaults PATCH succeeds.
  - Receipt fields come back with confidence.
- **Also check:** which model keys CU defaults accept for gpt-5 deployments. The script maps `gpt-4.1`,
  `gpt-4.1-mini` and `text-embedding-3-large`; the prebuilt analyzers may require those exact models.
- **ADR:** `docs/adr/NNNN-content-understanding-and-page-rasterization.md`. Spike 13 reuses the tokens per page.

## 4. Container Apps ingress: 8 MB body, 240 s

- **Goal:** confirm the Consumption ingress accepts an 8 MB multipart chunk and cuts requests at the fixed 240 s timeout.
- **Prerequisites:** provisioned environment; shell env loaded from `azd env get-values`. The script creates and then
  deletes a temporary `ca-spike-ingress` app running `python:3.12-slim`.
- **Command:** `bash spikes/s04_aca_ingress.sh` (about 10 minutes).
- **Pass:**
  - The 8 MB upload returns 200 with all bytes received.
  - A 200 s request returns 200.
  - A 250 s request fails near 240 s.
  - Also record the 12 MB result.
- **ADR:** `docs/adr/NNNN-container-apps-consumption-limits.md`.

## 5. Agent Framework workflow (Extractor ⇄ Verifier)

- **Goal:**
  - Run `agent-framework-core` 1.18 + `agent-framework-foundry` 1.13 `FoundryChatClient` with structured outputs.
  - Check the verifier loop stops within 2 rounds.
  - Confirm OpenTelemetry spans reach App Insights with `enable_sensitive_data=False`.
- **Prerequisites:** provisioned environment; `APPLICATIONINSIGHTS_CONNECTION_STRING` in `apps/api/.env`.
- **Command:**
  ```sh
  cd apps/api && uv run python ../../spikes/s05_agent_framework_workflow.py --pages ../../samples/golden/ca-northwind.pages.json
  ```
- **Pass:**
  - The workflow yields rows that each have a page and a quote.
  - Verifier rounds ≤ 2 and extractor passes ≤ 2.
  - In App Insights, `dependencies | where operation_Id == '<printed trace id>'` shows the gen_ai spans with no
    message content attributes.
- **Offline check (2026-09-16):** the workflow wiring (re-extract once, then stop) passed with a fake chat client.
- **ADR:** `docs/adr/NNNN-agent-framework-extraction-workflow.md`.

## 6. Language PII REST + Prompt Shields, keyless

- **Goal:** call `language/:analyze-text` (API `2026-05-01`), `contentsafety/text:shieldPrompt` and
  `contentsafety/image:analyze` on the AIServices endpoint with Entra ID tokens only.
- **Prerequisites:** provisioned environment; Cognitive Services User or Foundry User on the account.
- **Command:** `cd apps/api && uv run python ../../spikes/s06_language_pii_prompt_shields.py`
- **Pass:**
  - All three calls return 200 with bearer tokens.
  - The CA/AU categories are accepted.
  - `[MEMBER_A]`-style tokens are not reported as Person or Organization.
  - Test SIN/TFN values are detected.
  - The injected document is flagged, and the clean one is not.
  - Record the 6-document request status to confirm the batching cap.
- **ADR:** `docs/adr/NNNN-pii-two-tier-policy-and-prompt-shields.md`.

## 7. Foundry IQ MCP tool and continuous evaluation with `store=False`

- **Goal:**
  - (a) Can a prompt agent on gpt-5-mini use an AI Search knowledge base through the MCP tool with `store=False`?
  - (b) Does a continuous evaluation rule produce runs when responses are not stored?

  Adopting either replaces the defaults: the `search_public_knowledge` function tool and scheduled `evals.yml`.
- **Prerequisites:**
  - Provisioned environment.
  - `public-knowledge-v1` populated (`knowledge-ingest.yml`).
  - For (a), a project connection for the knowledge base MCP endpoint that uses the project managed identity. Create it
    in the portal and pass `--mcp-connection-id`.
  - The project identity has Search Index Data Reader (`search-roles.bicep`).
- **Command:**
  ```sh
  cd apps/api && uv run python ../../spikes/s07_foundry_iq_and_continuous_eval.py --mcp-connection-id <id> --wait-minutes 15
  ```
- **Pass (recorded, not pass/fail):**
  - `decision.adoptFoundryIq` is true only if the MCP call answers with citations under `store=False`.
  - `decision.adoptContinuousEval` is true only if eval runs appear.
- **ADR:** `docs/adr/NNNN-knowledge-retrieval-tool-choice.md` and `docs/adr/NNNN-continuous-evaluation.md`.

## 8. Quota for the three deployments

- **Goal:** confirm GlobalStandard quota and model versions in East US 2:
  - gpt-5-mini ≥ 200K TPM.
  - gpt-5-nano and text-embedding-3-small ≥ 100K TPM.
  - Fallback headroom for gpt-5.4-mini and gpt-4.1-mini.
- **Prerequisites:** `az login` (Reader).
- **Command:** `bash spikes/s08_quota.sh eastus2`
- **Pass:** the first three models are `ok`. If the chat model is short:
  - Request quota, or
  - `azd env set AZURE_CHAT_MODEL_CAPACITY <n>` (and re-check spike 13), or
  - Switch with `AZURE_CHAT_MODEL_NAME` / `AZURE_CHAT_MODEL_VERSION`.
- **ADR:** `docs/adr/NNNN-model-deployments-and-throughput.md`.

## 9. AI Search Free

- **Goal:**
  - Find an existing Free search service anywhere in the subscription; only one is allowed per subscription.
  - Otherwise create one in Canada Central, since East US 2 refuses new services.
  - Confirm the data plane works with Entra ID only: RBAC, `disableLocalAuth`, and the semantic ranker free plan
    together with an HNSW vector field.
- **Prerequisites:** `az login` with rights to create role assignments.
- **Command:**
  ```sh
  bash spikes/s09_search_free.sh            # detect only
  bash spikes/s09_search_free.sh --create   # create in canadacentral if none exists
  ```
  If a service exists, the script prints the `azd env set AZURE_EXISTING_SEARCH_SERVICE_NAME/_RESOURCE_GROUP` commands.
  It also prints the `--auth-options aadOrApiKey` fix when the reused service is still key-only.
- **Pass:**
  - Index create returns 201 and the semantic + vector query returns 200.
  - `@search.rerankerScore` is present.
  - The api-key request is rejected.
- **ADR:** `docs/adr/NNNN-ai-search-region-and-tier.md`. Include the data-residency note: the index holds public
  content only.

## 10. Indirect-attack guardrail on tool outputs

- **Goal:** send a canary injection inside a `<documents>`-wrapped `function_call_output` through the Responses API.
  Confirm the `benefura-guardrail` Indirect Attack filter blocks it with a 400 `content_filter`, and that a clean
  document passes.
- **Prerequisites:** provisioned environment (the guardrail is attached to the chat deployment).
- **Command:** `cd apps/api && uv run python ../../spikes/s10_indirect_attack_guardrail.py`
- **Pass:** the canary is blocked (400) and the clean document succeeds. If it is not blocked, the decision becomes
  "add Prompt Shields on `search_public_knowledge` results" and the API workstream implements it.
- **ADR:** `docs/adr/NNNN-stateless-responses-and-prompt-agents.md` (guardrail section) and `docs/threat-model.md`.

## 11. PII false-positive calibration

- **Goal:** run the Language PII check over the sample booklets and receipt text and apply the two-tier mapping.
  Confirm insurer and provider details only raise advisory issues, then pick the hard-tier confidence threshold.
- **Prerequisites:** provisioned environment; `samples/golden/*.pages.json` (or CU markdown saved from spike 3).
- **Command:**
  ```sh
  cd apps/api && uv run python ../../spikes/s11_pii_calibration.py \
    --pages ../../samples/golden/ca-northwind.pages.json ../../samples/golden/au-wattle.pages.json --min-confidence 0.8
  ```
- **Pass:** no hard-tier hit at or above the threshold on the fictional samples, and no alias token reported as
  Person or Organization. Record the per-category confidence ranges.
- **ADR:** `docs/adr/NNNN-pii-two-tier-policy-and-prompt-shields.md`.

## 12. CSP against the real static export on SWA

- **Goal:** confirm the `staticwebapp.config.json` CSP from `apps/web/scripts/write-swa-config.mjs` works with the
  Next.js 16 export on SWA:
  - No violations on any route.
  - `blob:` workers (pdf.js, tesseract.js) and wasm compilation are allowed.
  - Fetches to the API origin are allowed.
  - Fetches to other origins and `eval` are blocked.
- **Prerequisites:** web deployed (`azd deploy web`); `pnpm exec playwright install chromium`.
- **Command:**
  ```sh
  cd apps/web && node ../../spikes/s12_csp_check.mjs "$WEB_URL" "$API_URL"
  ```
  To rehearse locally, build with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm build`, run
  `npx @azure/static-web-apps-cli start out --port 4280` and the API, then check `http://localhost:4280`.
- **Pass:** every check prints `ok` and the script prints `PASS: s12`. Also walk through the redaction flow (OCR on a
  scanned page) in a browser with DevTools open.
- **ADR:** `docs/adr/NNNN-csp-for-static-export.md`.

## 13. Throughput: 60 pages at concurrency 4

- **Goal:**
  - Measure real `/api/plan/analyze-chunk` wall time and token usage for 5-page chunks with a 1-page overlap at
    concurrency 4.
  - Project a 60-page booklet against the 240 s limit, the 200K TPM deployment, and the M4 target (< 4 min).
- **Prerequisites:**
  - API deployed with the booklet pipeline (M4).
  - A redacted image-only booklet PDF.
  - Tokens per page from spike 3 (optional).
  - `EVALS_SHARED_SECRET` exported, so the run charges the `evals` budget namespace.
- **Command:**
  ```sh
  cd apps/api && uv run python ../../spikes/s13_throughput.py --api-url "$API_URL" \
    --pdf <redacted-booklet.pdf> --chunks 4 --tpm-k 200 --s03-result ../../spikes/results/s03-<stamp>.json
  ```
  With no `--pdf`, the script only projects from `--assume-chunk-seconds` and `--tokens-per-page`.
- **Pass:** p95 chunk ≤ 90 s (hard limit < 240 s); projected 60 pages < 4 min.
- **Planning note:** 60 pages is 15 chunks, which at concurrency 4 is 4 waves. A 90 s chunk therefore projects to about
  6 min. Meeting the < 4 min M4 target needs a p95 chunk ≤ 60 s, or higher concurrency.
- **ADR:** `docs/adr/NNNN-container-apps-consumption-limits.md` and `docs/adr/NNNN-model-deployments-and-throughput.md`.

## 14. Dependency trio: DONE

- **Goal:** `agent-framework` 1.18, `agent-framework-foundry` 1.13 and `azure-ai-projects` < 2.7 resolve from a clean
  `uv lock`.
- **Result (2026-09-16):**
  - The `agent-framework` 1.18 **meta-package** pins `azure-ai-contentunderstanding<1.1` through its Content
    Understanding connector. That conflicts with the plan's `azure-ai-contentunderstanding` 1.1 (GA API `2025-11-01`).
  - The project therefore depends on **`agent-framework-core==1.18.0`** + **`agent-framework-foundry==1.13.0`** +
    **`azure-ai-projects` 2.6.1** (`>=2.6,<2.7`). This resolves cleanly with `azure-ai-contentunderstanding` 1.1.0.
  - `apps/api/uv.lock` is committed with this set (also pulls `agent-framework-openai` 1.14.3 and `openai` 3.14.1).
- **Pass:** `cd apps/api && uv sync --locked` succeeds, and the spike imports above load (checked 2026-09-16).
- **ADR:** `docs/adr/NNNN-dependency-pins.md`. The plan's version table now reads `agent-framework-core` 1.18 instead of
  the meta-package.
