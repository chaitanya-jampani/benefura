// Shared keys are off: the API writes the budget table with its managed identity.

param name string
param location string
param tags object = {}

@description('Table holding per-namespace daily spend.')
param budgetTableName string = 'budgets'

@description('Disable public network access; the API reaches the table through a private endpoint (network.bicep).')
param enablePrivateNetworking bool = false

resource storage 'Microsoft.Storage/storageAccounts@2025-08-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    allowBlobPublicAccess: false
    allowCrossTenantReplication: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: enablePrivateNetworking ? 'Disabled' : 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: enablePrivateNetworking ? 'Deny' : 'Allow'
    }
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2025-08-01' = {
  parent: storage
  name: 'default'
}

resource budgetTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2025-08-01' = {
  parent: tableService
  name: budgetTableName
}

output id string = storage.id
output name string = storage.name
output tableEndpoint string = 'https://${storage.name}.table.${environment().suffixes.storage}'
output budgetTableName string = budgetTable.name
