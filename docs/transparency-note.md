# Transparency note

## What Benefura is

A demo assistant that helps a member of a Canadian extended health plan or an Australian private
health insurance policy understand their benefits. It reads a redacted booklet into a structured
plan, tracks usage against limits in the browser, drafts claims for the member to submit themselves,
and answers questions using the plan and a curated set of public government pages.

## Intended uses

- Understanding coverage, limits, waiting periods and deadlines in your own plan.
- Estimating what a service might be reimbursed, using the plan's rules.
- Keeping a personal record of claims and receipts.
- Finding public guidance, such as eligible medical expenses for tax or how Medicare interacts with
  private cover, with links to the source.

## Not intended for

- Insurance, tax, legal or medical advice, or eligibility decisions. The insurer's decision and the
  official documents always take precedence.
- Submitting claims. Benefura never contacts an insurer.
- Real personal information in the public demo. Use the fictional samples or a booklet with personal
  details removed.

## How it works

| Capability | Models and services | Human oversight |
|---|---|---|
| Booklet reading | Content Understanding layout; gpt-5-nano page triage; gpt-5-mini extractor with a gpt-5-mini verifier that checks each row against the page | Every row keeps its page and quote; low-confidence and unsupported rows are highlighted and editable before saving |
| Receipt reading | Content Understanding custom analyzer with field confidence | Fields pre-fill a claim the user reviews; manual entry is always available |
| Estimates and usage | Deterministic TypeScript rules, no model | Each estimate shows its steps |
| Chat | gpt-5-nano router; `benefura-plan-claims` and `benefura-knowledge` prompt agents on gpt-5-mini | Changes to claims need explicit approval; answers cite plan pages or public sources |

## Limitations

- Extraction can miss or misread rules, especially unusual tables, schedules by item code and
  conditions written in prose. Accuracy is measured on the fictional samples (target ≥90% of fields)
  and will be lower on real booklets with different layouts.
- The engine does not model coordination of benefits between two plans, loyalty tiers or Australian
  hospital claim amounts.
- Knowledge answers are limited to the indexed public pages, refreshed weekly, and may lag changes.
- OCR on low-quality scans reduces both redaction detection and extraction quality.
- The assistant can be wrong. It is instructed to say when the plan does not answer a question.

## Safety measures

Guardrails with jailbreak and indirect-attack blocking on every chat deployment; Prompt Shields on
all text read from documents; image moderation for receipts; a two-tier PII check on everything the
API receives; approval cards for data changes; per-agent tool allowlists and step caps; an off-topic
router; Foundry evaluations for groundedness, relevance, tool-call accuracy and safety; and red-team
runs with a published scorecard. See [threat model](threat-model.md) and [privacy](privacy.md).
