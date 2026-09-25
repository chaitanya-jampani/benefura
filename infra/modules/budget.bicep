// Billing backstop; the API's daily estimated-USD budgets are the first line.

param name string

@description('Monthly budget in the billing currency.')
param amount int = 20

param contactEmail string

@description('First day of a month (yyyy-MM-01). M0-verify: it cannot change after creation; pin it with AZURE_BUDGET_START_DATE.')
param startDate string

resource budget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: name
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: [
          contactEmail
        ]
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [
          contactEmail
        ]
      }
    }
  }
}

output id string = budget.id
