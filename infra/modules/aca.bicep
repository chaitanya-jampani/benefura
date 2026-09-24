param location string
param tags object = {}
param environmentName string
param containerAppName string

@description('Log Analytics workspace receiving environment logs via diagnostic settings.')
param logAnalyticsWorkspaceId string

@description('User-assigned identity resource ID, client ID (AZURE_CLIENT_ID).')
param identityId string
param identityClientId string

@description('Public image reference (no registry credentials), e.g. ghcr.io/<owner>/benefura-api:sha-<commit>.')
param image string

@description('Infrastructure subnet for VNet integration (empty = no VNet).')
param infrastructureSubnetId string = ''

@allowed([
  'live'
  'fake'
  'off'
])
param aiMode string = 'live'

param foundryProjectEndpoint string
param aiServicesEndpoint string
param searchEndpoint string
param searchIndex string = 'public-knowledge-v1'
param storageTableEndpoint string
param budgetTableName string = 'budgets'

@secure()
@description('App Insights connection string (APPLICATIONINSIGHTS_CONNECTION_STRING).')
param appInsightsConnectionString string

@description('Browser origins allowed by the API CORS policy (the SWA origin first).')
param corsOrigins string[]

@description('Model deployment names (CHAT_MODEL / NANO_MODEL / EMBEDDING_MODEL).')
param chatModel string
param nanoModel string
param embeddingModel string

@description('Pinned prompt agent versions as a JSON object {agentName: version}.')
param agentVersions string = '{}'

param dailyBudgetUsdExtraction string = '3.0'
param dailyBudgetUsdChat string = '2.0'
param dailyBudgetUsdEvals string = '5.0'

@secure()
@description('Shared secret that lets the eval harness use the evals budget namespace. Empty disables it.')
param evalsSharedSecret string = ''

@description('vCPU per replica (string so decimals survive).')
param cpu string = '1.0'
param memory string = '2Gi'

@description('Concurrent requests per replica before the HTTP scale rule adds one (max 1 replica anyway).')
param concurrentRequests int = 10

var useVnet = !empty(infrastructureSubnetId)

resource environment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    // Logs flow through diagnostic settings, so no workspace shared key is needed.
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    vnetConfiguration: useVnet
      ? {
          infrastructureSubnetId: infrastructureSubnetId
          internal: false
        }
      : null
    zoneRedundant: false
  }
}

resource environmentDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: environment
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

var secretEnv = empty(evalsSharedSecret)
  ? []
  : [
      {
        name: 'EVALS_SHARED_SECRET'
        secretRef: 'evals-shared-secret'
      }
    ]

// Names must match apps/api/app/config.py.
var plainEnv = [
  { name: 'AI_MODE', value: aiMode }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
  { name: 'AI_SERVICES_ENDPOINT', value: aiServicesEndpoint }
  { name: 'AZURE_CLIENT_ID', value: identityClientId }
  { name: 'SEARCH_ENDPOINT', value: searchEndpoint }
  { name: 'SEARCH_INDEX', value: searchIndex }
  { name: 'STORAGE_TABLE_ENDPOINT', value: storageTableEndpoint }
  { name: 'BUDGET_TABLE', value: budgetTableName }
  // The API owns CORS so it can expose x-benefura-trace-id; ingress CORS stays off. JSON array because
  // pydantic-settings JSON-decodes list fields.
  { name: 'CORS_ORIGINS', value: string(corsOrigins) }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
  { name: 'AGENT_VERSIONS', value: agentVersions }
  { name: 'CHAT_MODEL', value: chatModel }
  { name: 'NANO_MODEL', value: nanoModel }
  { name: 'EMBEDDING_MODEL', value: embeddingModel }
  { name: 'DAILY_BUDGET_USD_EXTRACTION', value: dailyBudgetUsdExtraction }
  { name: 'DAILY_BUDGET_USD_CHAT', value: dailyBudgetUsdChat }
  { name: 'DAILY_BUDGET_USD_EVALS', value: dailyBudgetUsdEvals }
  { name: 'OTEL_SERVICE_NAME', value: 'benefura-api' }
]

var secrets = concat(
  [
    {
      name: 'appinsights-connection-string'
      value: appInsightsConnectionString
    }
  ],
  empty(evalsSharedSecret)
    ? []
    : [
        {
          name: 'evals-shared-secret'
          value: evalsSharedSecret
        }
      ]
)

resource app 'Microsoft.App/containerApps@2026-01-01' = {
  name: containerAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 3
      // Consumption ingress has a fixed 240 s request timeout, so a booklet chunk targets <= 90 s.
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: concat(plainEnv, secretEnv)
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 2
              periodSeconds: 3
              timeoutSeconds: 3
              failureThreshold: 20
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: string(concurrentRequests)
              }
            }
          }
        ]
      }
    }
  }
}

output environmentId string = environment.id
output environmentName string = environment.name
output containerAppName string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
