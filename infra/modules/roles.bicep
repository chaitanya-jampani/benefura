import { roleIds } from 'role-ids.bicep'

param accountName string
param storageAccountName string
param appInsightsName string
param workspaceName string

@description('Principal ID of the API user-assigned managed identity.')
param apiPrincipalId string

@description('Principal ID of the Foundry project system-assigned identity.')
param projectPrincipalId string

@description('Object IDs of people/pipelines that provision, publish agents, create CU analyzers and ingest knowledge.')
param deployerPrincipalIds string[] = []

type assignment = {
  principalId: string
  @description('ServicePrincipal for managed identities (avoids replication races); empty lets ARM resolve it.')
  principalType: string
  roleId: string
}

resource account 'Microsoft.CognitiveServices/accounts@2026-05-01' existing = {
  name: accountName
}

resource storage 'Microsoft.Storage/storageAccounts@2025-08-01' existing = {
  name: storageAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-07-01' existing = {
  name: workspaceName
}

var accountAssignments assignment[] = concat(
  [
    { principalId: apiPrincipalId, principalType: 'ServicePrincipal', roleId: roleIds.foundryUser }
    { principalId: apiPrincipalId, principalType: 'ServicePrincipal', roleId: roleIds.cognitiveServicesUser }
    { principalId: projectPrincipalId, principalType: 'ServicePrincipal', roleId: roleIds.foundryUser }
  ],
  flatten(map(deployerPrincipalIds, id => [
    { principalId: id, principalType: '', roleId: roleIds.foundryUser }
    { principalId: id, principalType: '', roleId: roleIds.cognitiveServicesUser }
  ]))
)

var monitoringRoleIds = [
  roleIds.logAnalyticsReader
  roleIds.privilegedMonitoringDataReader
]

resource accountRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for a in accountAssignments: {
    scope: account
    name: guid(account.id, a.principalId, a.roleId)
    properties: {
      principalId: a.principalId
      principalType: empty(a.principalType) ? null : a.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
    }
  }
]

resource storageRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, apiPrincipalId, roleIds.storageTableDataContributor)
  properties: {
    principalId: apiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.storageTableDataContributor)
  }
}

resource projectAppInsightsRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for roleId in monitoringRoleIds: {
    scope: appInsights
    name: guid(appInsights.id, projectPrincipalId, roleId)
    properties: {
      principalId: projectPrincipalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
    }
  }
]

resource projectWorkspaceRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for roleId in monitoringRoleIds: {
    scope: workspace
    name: guid(workspace.id, projectPrincipalId, roleId)
    properties: {
      principalId: projectPrincipalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
    }
  }
]
