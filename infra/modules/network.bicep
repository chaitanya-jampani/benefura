// Not covered: AI Search Free and SWA Free have no private endpoints; prompt agents calling into the VNet need agent
// network injection; GitHub-hosted runners can't publish agents or run setup_cu against a private account.

param location string
param tags object = {}
param vnetName string
param resourceToken string

@description('Foundry (AIServices) account resource ID.')
param foundryAccountId string

@description('Storage account resource ID (table endpoint).')
param storageAccountId string

param addressPrefix string = '10.40.0.0/16'

@description('Container Apps workload-profiles environment needs at least a /27, delegated to Microsoft.App/environments.')
param acaSubnetPrefix string = '10.40.0.0/23'

param privateEndpointSubnetPrefix string = '10.40.2.0/24'

var foundryDnsZones = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
]

var storageTableDnsZone = 'privatelink.table.${environment().suffixes.storage}'

resource vnet 'Microsoft.Network/virtualNetworks@2025-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        addressPrefix
      ]
    }
    subnets: [
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: acaSubnetPrefix
          delegations: [
            {
              name: 'aca'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource acaSubnet 'Microsoft.Network/virtualNetworks/subnets@2025-05-01' existing = {
  parent: vnet
  name: 'snet-aca'
}

resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2025-05-01' existing = {
  parent: vnet
  name: 'snet-private-endpoints'
}

resource dnsZones 'Microsoft.Network/privateDnsZones@2024-06-01' = [
  for zone in concat(foundryDnsZones, [storageTableDnsZone]): {
    name: zone
    location: 'global'
    tags: tags
  }
]

resource dnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  for (zone, i) in concat(foundryDnsZones, [storageTableDnsZone]): {
    parent: dnsZones[i]
    name: 'link-${vnetName}'
    location: 'global'
    tags: tags
    properties: {
      registrationEnabled: false
      virtualNetwork: {
        id: vnet.id
      }
    }
  }
]

resource foundryPe 'Microsoft.Network/privateEndpoints@2025-05-01' = {
  name: 'pe-foundry-${resourceToken}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'foundry'
        properties: {
          privateLinkServiceId: foundryAccountId
          groupIds: [
            'account'
          ]
        }
      }
    ]
  }
}

resource foundryPeDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-05-01' = {
  parent: foundryPe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      for (zone, i) in foundryDnsZones: {
        name: replace(zone, '.', '-')
        properties: {
          privateDnsZoneId: dnsZones[i].id
        }
      }
    ]
  }
}

resource storagePe 'Microsoft.Network/privateEndpoints@2025-05-01' = {
  name: 'pe-table-${resourceToken}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'table'
        properties: {
          privateLinkServiceId: storageAccountId
          groupIds: [
            'table'
          ]
        }
      }
    ]
  }
}

resource storagePeDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-05-01' = {
  parent: storagePe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'table'
        properties: {
          privateDnsZoneId: dnsZones[length(foundryDnsZones)].id
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output acaSubnetId string = acaSubnet.id
output privateEndpointSubnetId string = peSubnet.id
