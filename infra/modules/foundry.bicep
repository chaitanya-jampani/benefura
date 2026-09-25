// M0-verify: API version 2026-05-01 for accounts, projects, deployments and connections.

@description('AIServices account name; also used as the custom subdomain (globally unique).')
param accountName string

@description('Foundry project name.')
param projectName string = 'benefura'

param location string
param tags object = {}

@description('Model deployments.')
param deployments deploymentType[]

@description('Disable public network access and route through private endpoints (see network.bicep).')
param enablePrivateNetworking bool = false

@description('Application Insights resource for the project connection (agent traces + Monitor tab).')
param appInsightsId string

@secure()
@description('Application Insights connection string (an ingestion endpoint, not a login).')
param appInsightsConnectionString string

@description('Azure AI Search endpoint for the CognitiveSearch (Entra ID) connection. Empty skips the connection.')
param searchEndpoint string = ''

@description('Azure AI Search resource ID for the connection metadata.')
param searchResourceId string = ''

@description('Azure AI Search location for the connection metadata.')
param searchLocation string = ''

type deploymentType = {
  @description('Deployment name the API uses (CHAT_MODEL / NANO_MODEL / EMBEDDING_MODEL).')
  name: string
  @description('Model name, e.g. gpt-5-mini.')
  model: string
  @description('Model version, e.g. 2025-08-07.')
  version: string
  @description('Deployment SKU, e.g. GlobalStandard.')
  sku: string
  @description('Capacity in K TPM.')
  capacity: int
  @description('Attach the Benefura guardrail (chat models only).')
  guardrail: bool
}

resource account 'Microsoft.CognitiveServices/accounts@2026-05-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: enablePrivateNetworking ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: enablePrivateNetworking ? 'Deny' : 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2026-05-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'Benefura'
    description: 'Privacy-first benefits assistant: prompt agents, evaluations and tracing.'
  }
}

module guardrail 'guardrail.bicep' = {
  name: 'guardrail'
  params: {
    accountName: account.name
  }
}

// Deployments on one account must be created one at a time.
@batchSize(1)
resource modelDeployments 'Microsoft.CognitiveServices/accounts/deployments@2026-05-01' = [
  for d in deployments: {
    parent: account
    name: d.name
    sku: {
      name: d.sku
      capacity: d.capacity
    }
    properties: {
      model: {
        format: 'OpenAI'
        name: d.model
        version: d.version
      }
      versionUpgradeOption: 'NoAutoUpgrade'
      raiPolicyName: d.guardrail ? guardrail.outputs.name : null
    }
  }
]

// Needed for project.telemetry.get_application_insights_connection_string() and agent traces in the portal.
// M0-verify: an 'ApiKey' connection carrying the connection string is the documented AppInsights shape.
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2026-05-01' = {
  parent: project
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsightsId
    authType: 'ApiKey'
    isSharedToAll: false
    credentials: {
      key: appInsightsConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsId
    }
  }
}

// AAD auth: the project identity gets Search Index Data Reader in search-roles.bicep.
resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2026-05-01' = if (!empty(searchEndpoint)) {
  parent: project
  name: 'public-knowledge-search'
  properties: {
    category: 'CognitiveSearch'
    target: searchEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: searchResourceId
      location: searchLocation
    }
  }
}

output accountId string = account.id
output accountName string = account.name
output accountPrincipalId string = account.identity.principalId
output projectName string = project.name
output projectPrincipalId string = project.identity.principalId
@description('https://<account>.cognitiveservices.azure.com/ (Language, Content Safety, Content Understanding REST).')
output aiServicesEndpoint string = account.properties.endpoint
@description('https://<account>.services.ai.azure.com/api/projects/<project> (AIProjectClient).')
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${project.name}'
output guardrailName string = guardrail.outputs.name
output deploymentNames string[] = [for (d, i) in deployments: modelDeployments[i].name]
