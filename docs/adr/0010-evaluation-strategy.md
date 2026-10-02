# 0010: Evaluation strategy

**Status:** Proposed. Continuous evaluation pending M0 spike 7.

## Context
Foundry offers cloud evaluations with built-in evaluators and continuous evaluation rules on agents.
Continuous evaluation may require stored responses, which conflicts with `store=false`
([ADR 0007](0007-stateless-conversations.md)).

## Decision
- A harness replays synthetic datasets through the real API under the `evals` budget namespace and
  writes JSONL with queries, responses, context, tool calls and tool definitions.
- `evals.yml` runs weekly and on demand: Foundry cloud evaluations (groundedness, relevance, document
  retrieval; intent resolution, task adherence, tool-call accuracy; safety evaluators), custom
  deterministic evaluators (extraction field accuracy ≥90%, engine-exact numbers, licence-allowed
  citations, no fake PII in payloads) and the AI Red Teaming Agent. Thresholds in
  `evals/thresholds.yaml` gate the run.
- If spike 7 shows continuous evaluation works with `store=false`, enable it on both agents in
  addition to the schedule; otherwise scheduled runs are the only mechanism.
