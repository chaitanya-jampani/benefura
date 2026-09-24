// Compile check only; azd deploys with main.parameters.json.
using '../main.bicep'

param environmentName = 'benefura-demo'
param location = 'eastus2'
param apiImage = 'ghcr.io/example/benefura-api:latest'
param enablePrivateNetworking = false
param existingSearchServiceName = ''
param searchLocation = 'canadacentral'
