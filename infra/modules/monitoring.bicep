// Metric names in the workbook and alert queries follow the telemetry contract in infra/README.md.

param location string
param tags object = {}
param workspaceName string
param appInsightsName string
param actionGroupName string
param resourceToken string

@description('Email for alerts. Empty creates the action group without receivers.')
param alertEmail string = ''

@description('Create the three scheduled query alert rules (about $1.50/month in total).')
param enableAlerts bool = true

@description('Daily estimated-USD budgets per namespace (strings so decimals survive; same values as the API env).')
param dailyBudgetUsdExtraction string = '3.0'
param dailyBudgetUsdChat string = '2.0'
param dailyBudgetUsdEvals string = '5.0'

@description('Server error rate (percent of requests, 15-minute window, at least 5 requests) that fires the 5xx alert.')
param serverErrorRatePct int = 5

@description('Safety events in a 15-minute window that fire the spike alert.')
param safetyEventSpikeThreshold int = 10

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-07-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    workspaceCapping: {
      // M0-verify: the minimum daily cap (Learn documents none).
      dailyQuotaGb: json('0.2')
    }
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    RetentionInDays: 30
    // Local auth stays on: Foundry agent traces and the API exporter ingest with the connection string.
    DisableLocalAuth: false
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

var workbookJson = replace(
  replace(
    replace(
      replace(loadTextContent('workbook/benefura.workbook.json'), '__APPINSIGHTS_ID__', appInsights.id),
      '__BUDGET_EXTRACTION_USD__',
      dailyBudgetUsdExtraction
    ),
    '__BUDGET_CHAT_USD__',
    dailyBudgetUsdChat
  ),
  '__BUDGET_EVALS_USD__',
  dailyBudgetUsdEvals
)

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'benefura-ai-operations-workbook')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: 'Benefura AI operations'
    category: 'workbook'
    sourceId: toLower(appInsights.id)
    serializedData: workbookJson
    version: 'Notebook/1.0'
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: actionGroupName
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'benefura'
    enabled: true
    emailReceivers: empty(alertEmail)
      ? []
      : [
          {
            name: 'owner'
            emailAddress: alertEmail
            useCommonAlertSchema: true
          }
        ]
  }
}

var budgetQuery = '''
let budgets = datatable(namespace: string, dailyBudgetUsd: real) [
    'demo-extraction', __BUDGET_EXTRACTION_USD__,
    'demo-chat', __BUDGET_CHAT_USD__,
    'evals', __BUDGET_EVALS_USD__
];
customMetrics
| where timestamp >= startofday(now())
| where name == 'benefura.budget.spend_usd'
| summarize spentUsd = sum(valueSum) by namespace = tostring(customDimensions['benefura.namespace'])
| join kind=inner budgets on namespace
| extend usedPct = round(100.0 * spentUsd / dailyBudgetUsd, 1)
| where usedPct >= 80
| project namespace, spentUsd, dailyBudgetUsd, usedPct
'''

var serverErrorQuery = '''
requests
| summarize total = count(), serverErrors = countif(toint(resultCode) >= 500)
| extend errorRatePct = iff(total == 0, 0.0, round(100.0 * serverErrors / total, 1))
| where total >= 5 and errorRatePct >= __THRESHOLD__
'''

var safetyQuery = '''
customMetrics
| where name == 'benefura.safety.events'
| summarize events = sum(valueSum) by kind = tostring(customDimensions['benefura.safety_kind'])
| summarize events = sum(events), kinds = make_set(kind)
| where events >= __THRESHOLD__
'''

var alertDefinitions = [
  {
    key: 'budget'
    displayName: 'Benefura daily AI budget at 80% or more'
    description: 'A budget namespace (demo-extraction, demo-chat or evals) has spent at least 80% of its daily estimated-USD budget. At 100% the API returns 429 budget_exhausted until 00:00 UTC.'
    severity: 2
    evaluationFrequency: 'PT1H'
    windowSize: 'P1D'
    query: replace(
      replace(replace(budgetQuery, '__BUDGET_EXTRACTION_USD__', dailyBudgetUsdExtraction), '__BUDGET_CHAT_USD__', dailyBudgetUsdChat),
      '__BUDGET_EVALS_USD__',
      dailyBudgetUsdEvals
    )
  }
  {
    key: 'server-errors'
    displayName: 'Benefura API 5xx rate'
    description: 'At least ${serverErrorRatePct}% of API requests returned 5xx in the last 15 minutes (minimum 5 requests).'
    severity: 1
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    query: replace(serverErrorQuery, '__THRESHOLD__', string(serverErrorRatePct))
  }
  {
    key: 'safety-spike'
    displayName: 'Benefura safety event spike'
    description: 'At least ${safetyEventSpikeThreshold} safety events (guardrail blocks, Prompt Shields attacks, unsafe images, hard-tier PII blocks) in 15 minutes.'
    severity: 2
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    query: replace(safetyQuery, '__THRESHOLD__', string(safetyEventSpikeThreshold))
  }
]

resource alerts 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [
  for a in alertDefinitions: if (enableAlerts) {
    name: 'alert-${a.key}-${resourceToken}'
    location: location
    tags: tags
    kind: 'LogAlert'
    properties: {
      displayName: a.displayName
      description: a.description
      severity: a.severity
      enabled: true
      scopes: [
        appInsights.id
      ]
      evaluationFrequency: a.evaluationFrequency
      windowSize: a.windowSize
      criteria: {
        allOf: [
          {
            query: a.query
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: false
      actions: {
        actionGroups: [
          actionGroup.id
        ]
      }
    }
  }
]

output workspaceId string = workspace.id
output workspaceName string = workspace.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output workbookId string = workbook.id
output actionGroupId string = actionGroup.id
