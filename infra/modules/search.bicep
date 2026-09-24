// The index holds public content only, so the service can sit outside East US 2, which refuses new search services.
// Free has no private endpoints and no managed identity, so ingestion pushes documents with Entra ID auth.

param name string

@description('Search location. Canada Central supports Free + semantic ranker (free plan).')
param location string = 'canadacentral'

param tags object = {}

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'free'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    publicNetworkAccess: 'Enabled'
    // Keys off: data-plane calls use Entra ID and the roles in search-roles.bicep.
    disableLocalAuth: true
    // M0-verify: semantic ranker free plan on the Free tier in the chosen region.
    semanticSearch: 'free'
  }
}

output id string = search.id
output name string = search.name
output location string = search.location
output endpoint string = 'https://${search.name}.search.windows.net'
