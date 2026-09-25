// Separate from roles.bicep because a reused search service can sit in another resource group.

import { roleIds } from 'role-ids.bicep'

param searchServiceName string

@description('Principal ID of the API user-assigned managed identity (query only).')
param apiPrincipalId string

@description('Principal ID of the Foundry project identity (CognitiveSearch connection, query only).')
param projectPrincipalId string

@description('Object IDs of deployers (CI OIDC + developer) that create the index and push documents.')
param deployerPrincipalIds string[] = []

type assignment = {
  principalId: string
  principalType: string
  roleId: string
}

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

var assignments assignment[] = concat(
  [
    { principalId: apiPrincipalId, principalType: 'ServicePrincipal', roleId: roleIds.searchIndexDataReader }
    { principalId: projectPrincipalId, principalType: 'ServicePrincipal', roleId: roleIds.searchIndexDataReader }
  ],
  flatten(map(deployerPrincipalIds, id => [
    { principalId: id, principalType: '', roleId: roleIds.searchIndexDataContributor }
    { principalId: id, principalType: '', roleId: roleIds.searchServiceContributor }
  ]))
)

resource searchRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for a in assignments: {
    scope: search
    name: guid(search.id, a.principalId, a.roleId)
    properties: {
      principalId: a.principalId
      principalType: empty(a.principalType) ? null : a.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
    }
  }
]
