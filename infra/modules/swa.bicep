// Headers (CSP etc.) come from out/staticwebapp.config.json, written by apps/web/scripts/write-swa-config.mjs.
// Free has no private endpoints or linked backends, so the browser calls the API cross-origin.

param name string

@description('SWA regions are limited (westus2, centralus, eastus2, westeurope, eastasia); content is served globally.')
param location string

param tags object = {}

resource swa 'Microsoft.Web/staticSites@2025-03-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Disabled'
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

output id string = swa.id
output name string = swa.name
output defaultHostname string = swa.properties.defaultHostname
output url string = 'https://${swa.properties.defaultHostname}'
