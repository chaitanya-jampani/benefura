// Indirect Attack covers instructions inside tool outputs, which is why search_public_knowledge wraps results in
// <documents>.
// M0-verify: filter names and 'mode' values against the 2026-05-01 raiPolicies schema.

param accountName string

@description('Content filter policy name referenced by deployments (raiPolicyName).')
param policyName string = 'benefura-guardrail'

@allowed([
  'Low'
  'Medium'
  'High'
])
@description('Severity threshold for harm categories; content at or above it is blocked.')
param harmSeverityThreshold string = 'Medium'

var harmCategories = [
  'Hate'
  'Sexual'
  'Selfharm'
  'Violence'
]

var harmFilters = flatten(map(harmCategories, category => [
  {
    name: category
    enabled: true
    blocking: true
    severityThreshold: harmSeverityThreshold
    source: 'Prompt'
  }
  {
    name: category
    enabled: true
    blocking: true
    severityThreshold: harmSeverityThreshold
    source: 'Completion'
  }
]))

var attackFilters = [
  {
    name: 'Jailbreak'
    enabled: true
    blocking: true
    source: 'Prompt'
  }
  {
    name: 'Indirect Attack'
    enabled: true
    blocking: true
    source: 'Prompt'
  }
  {
    name: 'Protected Material Text'
    enabled: true
    blocking: true
    source: 'Completion'
  }
  {
    name: 'Protected Material Code'
    enabled: true
    blocking: true
    source: 'Completion'
  }
]

resource account 'Microsoft.CognitiveServices/accounts@2026-05-01' existing = {
  name: accountName
}

resource policy 'Microsoft.CognitiveServices/accounts/raiPolicies@2026-05-01' = {
  parent: account
  name: policyName
  properties: {
    basePolicyName: 'Microsoft.DefaultV2'
    mode: 'Blocking'
    contentFilters: concat(harmFilters, attackFilters)
  }
}

output name string = policy.name
