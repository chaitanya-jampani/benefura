// Compile and what-if check only (deploy.yml); the demo never deploys it.
using '../main.bicep'

param environmentName = 'benefura-private'
param location = 'eastus2'
param apiImage = 'ghcr.io/example/benefura-api:latest'
param enablePrivateNetworking = true
param enableAlerts = false
