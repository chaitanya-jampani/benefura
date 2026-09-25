targetScope = 'subscription'

@minLength(1)
@maxLength(40)
@description('azd environment name; used for the resource group name and the resource token.')
param environmentName string

@minLength(1)
@description('Primary location for all resources except AI Search.')
param location string

@description('Object ID of whoever runs azd (developer locally, CI service principal in GitHub Actions).')
param principalId string = ''

@description('Optional developer object ID, so CI runs keep the developer role assignments.')
param developerPrincipalId string = ''

@description('Optional CI OIDC service principal object ID, so local runs keep the CI role assignments.')
param ciPrincipalId string = ''

@description('Public GHCR image for the API, e.g. ghcr.io/<owner>/benefura-api:sha-<commit>.')
param apiImage string

@allowed([
  'live'
  'fake'
  'off'
])
param aiMode string = 'live'

@description('Pinned prompt agent versions as comma-separated name=version pairs (JSON would break azd parameter substitution). The app receives them as the AGENT_VERSIONS JSON object.')
param agentVersions string = ''

@description('Extra browser origins for CORS besides the Static Web App (comma-separated), e.g. http://localhost:3000.')
param extraCorsOrigins string = ''

@description('Daily estimated-USD budgets per namespace (strings so decimals survive).')
param dailyBudgetUsdExtraction string = '3.0'
param dailyBudgetUsdChat string = '2.0'
param dailyBudgetUsdEvals string = '5.0'

@secure()
@description('Shared secret for the evals budget namespace (GitHub secret EVALS_SHARED_SECRET). Empty disables it.')
param evalsSharedSecret string = ''

// Chat model fallbacks: gpt-5.4-mini, then gpt-4.1-mini (change name and version together).
// M0-verify: model versions, GlobalStandard availability in East US 2 and quota (>= 200K TPM for the chat model).
@description('Chat/extraction model (Extractor, Verifier, prompt agents).')
param chatModelName string = 'gpt-5-mini'
param chatModelVersion string = '2025-08-07'
param chatDeploymentName string = 'gpt-5-mini'
param chatDeploymentSku string = 'GlobalStandard'
@description('Capacity in K TPM (200 = 200K TPM).')
param chatModelCapacity int = 200

@description('Small model (router, page triage, knowledge enrichment).')
param nanoModelName string = 'gpt-5-nano'
param nanoModelVersion string = '2025-08-07'
param nanoDeploymentName string = 'gpt-5-nano'
param nanoDeploymentSku string = 'GlobalStandard'
param nanoModelCapacity int = 100

@description('Embedding model (512 dimensions requested at call time).')
param embeddingModelName string = 'text-embedding-3-small'
param embeddingModelVersion string = '1'
param embeddingDeploymentName string = 'text-embedding-3-small'
param embeddingDeploymentSku string = 'GlobalStandard'
param embeddingModelCapacity int = 100

@description('Reuse an existing Free search service (only one Free service per subscription). Empty creates one.')
param existingSearchServiceName string = ''

@description('Resource group of the reused search service (defaults to the Benefura resource group).')
param existingSearchResourceGroupName string = ''

@description('Location for a new search service. East US 2 currently refuses new search services.')
param searchLocation string = 'canadacentral'

@description('Static Web Apps region (Free is available in eastus2).')
param staticWebAppLocation string = 'eastus2'

@description('Email for Azure Monitor alerts and the cost budget. Empty skips the budget and email receivers.')
param alertEmail string = ''

param enableAlerts bool = true

@description('Monthly resource group cost budget (billing currency).')
param monthlyBudgetAmount int = 20

@description('Budget start date (yyyy-MM-01). Empty uses the current month; pin it with AZURE_BUDGET_START_DATE after the first deployment.')
param budgetStartDate string = ''

@description('Do not set: current month, used when budgetStartDate is empty (utcNow is only allowed in parameter defaults).')
param currentMonthStart string = utcNow('yyyy-MM-01')

@description('Private networking (VNet, private endpoints, publicNetworkAccess Disabled). Not used by the public demo.')
param enablePrivateNetworking bool = false

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  app: 'benefura'
}
var resourceGroupName = 'rg-${environmentName}'
var reuseSearch = !empty(existingSearchServiceName)
var searchResourceGroupName = reuseSearch && !empty(existingSearchResourceGroupName)
  ? existingSearchResourceGroupName
  : resourceGroupName
var deployerPrincipalIds = filter(union([principalId], [developerPrincipalId], [ciPrincipalId]), id => !empty(id))
var extraOrigins = filter(map(split(extraCorsOrigins, ','), o => trim(o)), o => !empty(o))
var agentVersionPairs = filter(map(split(agentVersions, ','), p => trim(p)), p => contains(p, '='))
var agentVersionsJson = string(toObject(agentVersionPairs, p => trim(split(p, '=')[0]), p => trim(split(p, '=')[1])))

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: {
    location: location
    tags: tags
    workspaceName: 'log-${resourceToken}'
    appInsightsName: 'appi-${resourceToken}'
    actionGroupName: 'ag-${resourceToken}'
    resourceToken: resourceToken
    alertEmail: alertEmail
    enableAlerts: enableAlerts
    dailyBudgetUsdExtraction: dailyBudgetUsdExtraction
    dailyBudgetUsdChat: dailyBudgetUsdChat
    dailyBudgetUsdEvals: dailyBudgetUsdEvals
  }
}

module budget 'modules/budget.bicep' = if (!empty(alertEmail)) {
  scope: rg
  name: 'budget'
  params: {
    name: 'budget-${environmentName}'
    amount: monthlyBudgetAmount
    contactEmail: alertEmail
    startDate: empty(budgetStartDate) ? currentMonthStart : budgetStartDate
  }
}

module storage 'modules/storage.bicep' = {
  scope: rg
  name: 'storage'
  params: {
    name: 'st${resourceToken}'
    location: location
    tags: tags
    enablePrivateNetworking: enablePrivateNetworking
  }
}

module newSearch 'modules/search.bicep' = if (!reuseSearch) {
  scope: rg
  name: 'search'
  params: {
    name: 'srch-${resourceToken}'
    location: searchLocation
    tags: tags
  }
}

resource existingSearch 'Microsoft.Search/searchServices@2025-05-01' existing = if (reuseSearch) {
  scope: resourceGroup(searchResourceGroupName)
  name: existingSearchServiceName
}

var searchName = reuseSearch ? existingSearchServiceName : 'srch-${resourceToken}'
var searchEndpoint = 'https://${searchName}.search.windows.net'
var searchId = reuseSearch ? existingSearch.id : newSearch!.outputs.id
var searchLocationActual = reuseSearch ? existingSearch!.location : newSearch!.outputs.location

module foundry 'modules/foundry.bicep' = {
  scope: rg
  name: 'foundry'
  params: {
    accountName: 'aif-${resourceToken}'
    projectName: 'benefura'
    location: location
    tags: tags
    enablePrivateNetworking: enablePrivateNetworking
    appInsightsId: monitoring.outputs.appInsightsId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    searchEndpoint: searchEndpoint
    searchResourceId: searchId
    searchLocation: searchLocationActual
    deployments: [
      {
        name: chatDeploymentName
        model: chatModelName
        version: chatModelVersion
        sku: chatDeploymentSku
        capacity: chatModelCapacity
        guardrail: true
      }
      {
        name: nanoDeploymentName
        model: nanoModelName
        version: nanoModelVersion
        sku: nanoDeploymentSku
        capacity: nanoModelCapacity
        guardrail: true
      }
      {
        name: embeddingDeploymentName
        model: embeddingModelName
        version: embeddingModelVersion
        sku: embeddingDeploymentSku
        capacity: embeddingModelCapacity
        guardrail: false
      }
    ]
  }
}

module apiIdentity 'modules/identity.bicep' = {
  scope: rg
  name: 'api-identity'
  params: {
    name: 'id-api-${resourceToken}'
    location: location
    tags: tags
  }
}

module roles 'modules/roles.bicep' = {
  scope: rg
  name: 'roles'
  params: {
    accountName: foundry.outputs.accountName
    storageAccountName: storage.outputs.name
    appInsightsName: monitoring.outputs.appInsightsName
    workspaceName: monitoring.outputs.workspaceName
    apiPrincipalId: apiIdentity.outputs.principalId
    projectPrincipalId: foundry.outputs.projectPrincipalId
    deployerPrincipalIds: deployerPrincipalIds
  }
}

module searchRoles 'modules/search-roles.bicep' = {
  scope: resourceGroup(searchResourceGroupName)
  name: 'search-roles-${resourceToken}'
  params: {
    searchServiceName: reuseSearch ? existingSearchServiceName : newSearch!.outputs.name
    apiPrincipalId: apiIdentity.outputs.principalId
    projectPrincipalId: foundry.outputs.projectPrincipalId
    deployerPrincipalIds: deployerPrincipalIds
  }
}

module swa 'modules/swa.bicep' = {
  scope: rg
  name: 'swa'
  params: {
    name: 'swa-${resourceToken}'
    location: staticWebAppLocation
    tags: tags
  }
}

module network 'modules/network.bicep' = if (enablePrivateNetworking) {
  scope: rg
  name: 'network'
  params: {
    location: location
    tags: tags
    vnetName: 'vnet-${resourceToken}'
    resourceToken: resourceToken
    foundryAccountId: foundry.outputs.accountId
    storageAccountId: storage.outputs.id
  }
}

module aca 'modules/aca.bicep' = {
  scope: rg
  name: 'aca'
  params: {
    location: location
    tags: tags
    environmentName: 'cae-${resourceToken}'
    containerAppName: 'ca-api-${resourceToken}'
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    identityId: apiIdentity.outputs.id
    identityClientId: apiIdentity.outputs.clientId
    image: apiImage
    infrastructureSubnetId: enablePrivateNetworking ? network!.outputs.acaSubnetId : ''
    aiMode: aiMode
    foundryProjectEndpoint: foundry.outputs.projectEndpoint
    aiServicesEndpoint: foundry.outputs.aiServicesEndpoint
    searchEndpoint: searchEndpoint
    storageTableEndpoint: storage.outputs.tableEndpoint
    budgetTableName: storage.outputs.budgetTableName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    corsOrigins: concat([swa.outputs.url], extraOrigins)
    chatModel: chatDeploymentName
    nanoModel: nanoDeploymentName
    embeddingModel: embeddingDeploymentName
    agentVersions: agentVersionsJson
    dailyBudgetUsdExtraction: dailyBudgetUsdExtraction
    dailyBudgetUsdChat: dailyBudgetUsdChat
    dailyBudgetUsdEvals: dailyBudgetUsdEvals
    evalsSharedSecret: evalsSharedSecret
  }
  // The first revision needs its data-plane roles (propagation can still take a few minutes).
  dependsOn: [
    roles
    searchRoles
  ]
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name

@description('API base URL (the web build uses it as NEXT_PUBLIC_API_BASE_URL).')
output API_URL string = aca.outputs.url
@description('API_URL under the name the Next.js build reads, for azd build steps that receive the azd environment.')
output NEXT_PUBLIC_API_BASE_URL string = aca.outputs.url
output WEB_URL string = swa.outputs.url
output AZURE_STATIC_WEB_APP_NAME string = swa.outputs.name
output AZURE_CONTAINER_APP_NAME string = aca.outputs.containerAppName
output AZURE_CONTAINER_APPS_ENVIRONMENT_NAME string = aca.outputs.environmentName
output API_IMAGE string = apiImage

output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output AI_SERVICES_ENDPOINT string = foundry.outputs.aiServicesEndpoint
output AZURE_AI_ACCOUNT_NAME string = foundry.outputs.accountName
output AZURE_AI_PROJECT_NAME string = foundry.outputs.projectName
output CHAT_MODEL string = chatDeploymentName
output NANO_MODEL string = nanoDeploymentName
output EMBEDDING_MODEL string = embeddingDeploymentName

output SEARCH_ENDPOINT string = searchEndpoint
output AZURE_SEARCH_SERVICE_NAME string = searchName
output AZURE_SEARCH_RESOURCE_GROUP string = searchResourceGroupName

output STORAGE_TABLE_ENDPOINT string = storage.outputs.tableEndpoint
output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.name

@description('Client ID of the API managed identity (the container app reads it as AZURE_CLIENT_ID).')
output AZURE_CLIENT_ID string = apiIdentity.outputs.clientId
output API_IDENTITY_CLIENT_ID string = apiIdentity.outputs.clientId

output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
output AZURE_APP_INSIGHTS_NAME string = monitoring.outputs.appInsightsName
output AZURE_LOG_ANALYTICS_WORKSPACE_NAME string = monitoring.outputs.workspaceName
