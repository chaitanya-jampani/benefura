"""Generate infra/modules/workbook/benefura.workbook.json; ``--check`` fails when it is out of date.

monitoring.bicep replaces the ``__APPINSIGHTS_ID__`` and ``__BUDGET_*_USD__`` tokens. Metric names follow the
telemetry contract in ``infra/README.md``.
"""

import json
import sys
from pathlib import Path

AI = "__APPINSIGHTS_ID__"
RT = "microsoft.insights/components"

items = []
counter = 0


def _name(prefix):
    global counter
    counter += 1
    return f"{prefix} - {counter}"


def text(md):
    items.append({"type": 1, "content": {"json": md}, "name": _name("text")})


def query(title, kql, viz="table", width=None, time_param=True, extra=None, size=0, no_data=None):
    content = {
        "version": "KqlItem/1.0",
        "query": kql.strip(),
        "size": size,
        "title": title,
        "queryType": 0,
        "resourceType": RT,
        "crossComponentResources": [AI],
        "visualization": viz,
    }
    if time_param:
        content["timeContextFromParameter"] = "TimeRange"
    else:
        content["timeContext"] = {"durationMs": 86400000}
    if no_data:
        content["noDataMessage"] = no_data
        content["noDataMessageStyle"] = 3
    if extra:
        content.update(extra)
    item = {"type": 3, "content": content, "name": _name("query")}
    if width:
        item["customWidth"] = str(width)
    items.append(item)


NO_METRICS = "No data yet. Metrics appear after the API records them (see infra/README.md, telemetry contract)."

text(
    "# Benefura: AI operations\n"
    "Token usage by agent and model, latency by pipeline step, safety signals, errors and daily budget spend. "
    "Metric and attribute names follow the telemetry contract in `infra/README.md`. "
    "Content recording is off: no rows here contain user text, PII values or alias maps."
)

items.append(
    {
        "type": 9,
        "content": {
            "version": "KqlParameterItem/1.0",
            "crossComponentResources": [AI],
            "parameters": [
                {
                    "id": "7b0c3c2e-2a5e-4b8f-9d3f-1f0d5a6c7e01",
                    "version": "KqlParameterItem/1.0",
                    "name": "TimeRange",
                    "label": "Time range",
                    "type": 4,
                    "isRequired": True,
                    "value": {"durationMs": 86400000},
                    "typeSettings": {
                        "selectableValues": [
                            {"durationMs": 3600000},
                            {"durationMs": 14400000},
                            {"durationMs": 43200000},
                            {"durationMs": 86400000},
                            {"durationMs": 259200000},
                            {"durationMs": 604800000},
                            {"durationMs": 2592000000},
                        ],
                        "allowCustom": True,
                    },
                }
            ],
            "style": "pills",
            "queryType": 0,
            "resourceType": RT,
        },
        "name": "parameters",
    }
)

text("## Overview")
query(
    "Requests, server errors and safety events",
    """
let reqs = requests
| summarize requests = count(), serverErrors = countif(toint(resultCode) >= 500), p95Ms = round(percentile(duration, 95), 0);
let safety = customMetrics
| where name == 'benefura.safety.events'
| summarize safetyEvents = sum(valueSum);
let tokens = customMetrics
| where name == 'benefura.model.tokens'
| summarize tokens = sum(valueSum);
reqs | extend k = 1
| join kind=fullouter (safety | extend k = 1) on k
| join kind=fullouter (tokens | extend k = 1) on k
| project requests = coalesce(requests, 0), serverErrors = coalesce(serverErrors, 0), p95Ms,
          safetyEvents = coalesce(safetyEvents, 0.0), tokens = coalesce(tokens, 0.0)
""",
)
query(
    "Requests by status class",
    """
requests
| summarize requests = count() by bin(timestamp, {TimeRange:grain}), status = strcat(substring(resultCode, 0, 1), 'xx')
""",
    viz="timechart",
    width=50,
)
query(
    "API latency by endpoint (ms)",
    """
requests
| summarize requests = count(), p50Ms = round(percentile(duration, 50), 0), p95Ms = round(percentile(duration, 95), 0),
            failures = countif(success == false) by endpoint = name
| order by requests desc
""",
    width=50,
)

text("## Token usage by agent and model")
query(
    "Tokens over time (agent / model)",
    """
customMetrics
| where name == 'benefura.model.tokens'
| extend agent = tostring(customDimensions['benefura.agent']), model = tostring(customDimensions['benefura.model'])
| summarize tokens = sum(valueSum) by bin(timestamp, {TimeRange:grain}), series = strcat(agent, ' / ', model)
""",
    viz="timechart",
    width=50,
    no_data=NO_METRICS,
)
query(
    "Tokens by agent, model and type",
    """
customMetrics
| where name == 'benefura.model.tokens'
| extend agent = tostring(customDimensions['benefura.agent']), model = tostring(customDimensions['benefura.model']),
         tokenType = tostring(customDimensions['benefura.token_type'])
| summarize tokens = sum(valueSum) by agent, model, tokenType
| evaluate pivot(tokenType, sum(tokens))
| order by agent asc, model asc
""",
    width=50,
    no_data=NO_METRICS,
)
query(
    "Model call latency by agent and model (ms)",
    """
customMetrics
| where name == 'benefura.model.duration'
| extend agent = tostring(customDimensions['benefura.agent']), model = tostring(customDimensions['benefura.model']),
         n = tolong(valueCount), avgMs = valueSum / valueCount
| where n > 0
| summarize calls = sum(n), p50Ms = round(percentilew(avgMs, n, 50), 0), p95Ms = round(percentilew(avgMs, n, 95), 0),
            maxMs = round(max(valueMax), 0) by agent, model
| order by p95Ms desc
""",
    no_data=NO_METRICS,
)

text(
    "## Latency by pipeline step\n"
    "Steps: `router`, `cu`, `pii`, `shields`, `content_safety`, `triage`, `extractor`, `verifier`, `assemble`, "
    "`search`, `embedding`, `model`. Percentiles are weighted over exported histogram aggregates "
    "(exact at demo volume)."
)
query(
    "p50 / p95 by step (ms)",
    """
customMetrics
| where name == 'benefura.step.duration'
| extend step = tostring(customDimensions['benefura.step']), n = tolong(valueCount), avgMs = valueSum / valueCount
| where n > 0
| summarize samples = sum(n), p50Ms = round(percentilew(avgMs, n, 50), 0), p95Ms = round(percentilew(avgMs, n, 95), 0),
            maxMs = round(max(valueMax), 0) by step
| extend ord = case(step == 'router', 1, step == 'cu', 2, step == 'pii', 3, step == 'shields', 4,
                    step == 'content_safety', 5, step == 'triage', 6, step == 'extractor', 7, step == 'verifier', 8,
                    step == 'assemble', 9, step == 'search', 10, step == 'embedding', 11, step == 'model', 12, 20)
| order by ord asc
| project-away ord
""",
    width=50,
    no_data=NO_METRICS,
)
query(
    "p95 by step over time (ms)",
    """
customMetrics
| where name == 'benefura.step.duration'
| extend step = tostring(customDimensions['benefura.step']), n = tolong(valueCount), avgMs = valueSum / valueCount
| where n > 0
| summarize p95Ms = percentilew(avgMs, n, 95) by bin(timestamp, {TimeRange:grain}), step
""",
    viz="timechart",
    width=50,
    no_data=NO_METRICS,
)
query(
    "Slowest dependencies and spans (p95 ms)",
    """
dependencies
| extend step = tostring(customDimensions['benefura.step'])
| summarize calls = count(), p50Ms = round(percentile(duration, 50), 0), p95Ms = round(percentile(duration, 95), 0),
            failures = countif(success == false) by type, target, name, step
| top 25 by p95Ms desc
""",
)

text("## Router and tools")
query(
    "Router decisions",
    """
customMetrics
| where name == 'benefura.router.decisions'
| summarize decisions = sum(valueSum) by route = tostring(customDimensions['benefura.route'])
""",
    viz="piechart",
    width=33,
    no_data=NO_METRICS,
)
query(
    "Tool calls",
    """
let calls = customMetrics
| where name == 'benefura.tool.calls'
| extend tool = tostring(customDimensions['benefura.tool']), executor = tostring(customDimensions['benefura.executor']),
         outcome = tostring(customDimensions['benefura.outcome'])
| summarize calls = sum(valueSum), errors = sumif(valueSum, outcome in ('error', 'rejected')) by tool, executor;
let durations = customMetrics
| where name == 'benefura.tool.duration'
| extend tool = tostring(customDimensions['benefura.tool']), executor = tostring(customDimensions['benefura.executor']),
         n = tolong(valueCount), avgMs = valueSum / valueCount
| where n > 0
| summarize p95Ms = round(percentilew(avgMs, n, 95), 0) by tool, executor;
calls
| join kind=leftouter durations on tool, executor
| project tool, executor, calls, errors, errorRatePct = round(100.0 * errors / calls, 1), p95Ms
| order by calls desc
""",
    width=67,
    no_data=NO_METRICS,
)

text(
    "## Safety signals\n"
    "Guardrail blocks (content filter, jailbreak, indirect attack), Prompt Shields document attacks, unsafe images, "
    "PII hits by category and tier (never values) and verifier verdicts."
)
query(
    "Safety events over time",
    """
customMetrics
| where name == 'benefura.safety.events'
| summarize events = sum(valueSum) by bin(timestamp, {TimeRange:grain}), kind = tostring(customDimensions['benefura.safety_kind'])
""",
    viz="timechart",
    width=50,
    no_data=NO_METRICS,
)
query(
    "Safety events by kind and surface",
    """
customMetrics
| where name == 'benefura.safety.events'
| summarize events = sum(valueSum) by kind = tostring(customDimensions['benefura.safety_kind']),
                                      surface = tostring(customDimensions['benefura.surface'])
| order by events desc
""",
    width=50,
    no_data=NO_METRICS,
)
query(
    "PII hits by category and tier",
    """
customMetrics
| where name == 'benefura.pii.hits'
| summarize hits = sum(valueSum) by category = tostring(customDimensions['benefura.pii_category']),
                                    tier = tostring(customDimensions['benefura.pii_tier'])
| order by hits desc
""",
    viz="barchart",
    width=50,
    no_data=NO_METRICS,
)
query(
    "Verifier verdicts",
    """
customMetrics
| where name == 'benefura.verifier.verdicts'
| summarize rows = sum(valueSum) by verdict = tostring(customDimensions['benefura.verdict'])
""",
    viz="piechart",
    width=50,
    no_data=NO_METRICS,
)

text("## Knowledge search")
query(
    "Search health",
    """
let q = customMetrics
| where name == 'benefura.search.queries'
| summarize queries = sum(valueSum), zeroResult = sumif(valueSum, tostring(customDimensions['benefura.zero_results']) == 'true');
let lat = customMetrics
| where name == 'benefura.search.duration'
| extend n = tolong(valueCount), avgMs = valueSum / valueCount
| where n > 0
| summarize p50Ms = round(percentilew(avgMs, n, 50), 0), p95Ms = round(percentilew(avgMs, n, 95), 0);
let score = customMetrics
| where name == 'benefura.search.top_score'
| summarize avgTopScore = round(sum(valueSum) / sum(valueCount), 3);
q | extend k = 1
| join kind=fullouter (lat | extend k = 1) on k
| join kind=fullouter (score | extend k = 1) on k
| project queries, zeroResultRatePct = round(100.0 * zeroResult / queries, 1), p50Ms, p95Ms, avgTopScore
""",
    no_data=NO_METRICS,
)

text("## Errors")
query(
    "Failed requests by endpoint and status",
    """
requests
| where toint(resultCode) >= 400
| summarize failures = count() by endpoint = name, resultCode
| order by failures desc
""",
    width=50,
)
query(
    "Exceptions by type",
    """
exceptions
| summarize exceptions = count(), lastSeen = max(timestamp) by type, method
| order by exceptions desc
""",
    width=50,
)
query(
    "Error and critical traces",
    """
traces
| where severityLevel >= 3
| summarize traces = count(), lastSeen = max(timestamp) by message = substring(message, 0, 160), cloud_RoleName
| top 20 by traces desc
""",
)

text(
    "## Daily budget (estimated USD)\n"
    "Spend recorded by the API per namespace since 00:00 UTC. The authoritative counter is the `budgets` table; "
    "the API returns 429 `budget_exhausted` at 100%."
)
query(
    "Spend today vs daily budget",
    """
let budgets = datatable(namespace: string, dailyBudgetUsd: real) [
    'demo-extraction', __BUDGET_EXTRACTION_USD__,
    'demo-chat', __BUDGET_CHAT_USD__,
    'evals', __BUDGET_EVALS_USD__
];
customMetrics
| where timestamp >= startofday(now())
| where name == 'benefura.budget.spend_usd'
| summarize spentUsd = sum(valueSum) by namespace = tostring(customDimensions['benefura.namespace'])
| join kind=rightouter budgets on namespace
| project namespace = namespace1, spentUsd = round(coalesce(spentUsd, 0.0), 4), dailyBudgetUsd,
          usedPct = round(100.0 * coalesce(spentUsd, 0.0) / dailyBudgetUsd, 1)
""",
    width=50,
    time_param=False,
)
query(
    "Spend over time by namespace (USD)",
    """
customMetrics
| where name == 'benefura.budget.spend_usd'
| summarize spentUsd = sum(valueSum) by bin(timestamp, {TimeRange:grain}), namespace = tostring(customDimensions['benefura.namespace'])
""",
    viz="timechart",
    width=50,
    no_data=NO_METRICS,
)

workbook = {
    "version": "Notebook/1.0",
    "items": items,
    "fallbackResourceIds": [AI],
    "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json",
}

OUT = Path(__file__).resolve().parents[1] / "modules" / "workbook" / "benefura.workbook.json"
rendered = json.dumps(workbook, indent=2) + "\n"

if "--check" in sys.argv:
    if not OUT.exists() or OUT.read_text() != rendered:
        print(f"{OUT} is out of date; run: python3 infra/scripts/gen_workbook.py", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(rendered)
print(f"wrote {OUT} ({len(items)} items)")
