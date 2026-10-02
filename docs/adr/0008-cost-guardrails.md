# 0008: Cost guardrails

**Status:** Accepted

## Context
The demo is public and unauthenticated. A single 60-page booklet costs roughly $0.60–0.90 (CU
layout at $5 per 1,000 pages, about three LLM passes, Language and Content Safety text records); a
chat turn roughly $0.002–0.01. Idle cost should be close to zero.

## Decision
- Budgets are tracked in **estimated dollars** per UTC day in Table storage, in separate namespaces:
  `demo-extraction`, `demo-chat` and `evals`. An upload burst can't silence chat, and a weekly eval
  run can't lock out the demo.
- Abuse caps: per-IP rate limit, ≤120 pages per booklet, ≤3 booklets per IP per day.
- Language and Content Safety run on the same **S0** AIServices account (keyless, one endpoint) and
  are counted in the budget. Separate F0 resources would be cheaper for low volume but add endpoints
  and keys-or-roles to manage.
- Container Apps scale to zero; Static Web Apps and AI Search on Free; images on **GHCR** instead of
  ACR (saves about $5 a month and the AcrPull role).
- A resource-group budget alert backs this up.

## Consequences
When a namespace is spent the API returns 429 `budget_exhausted` with `Retry-After` until midnight
UTC; the dashboard, engine and demo data keep working. Prices live in one table in `app/budget.py`.
