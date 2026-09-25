#!/usr/bin/env bash
# AZURE_CLIENT_ID is not exported: in workflows it is the CI app registration used for OIDC, while the Bicep output
# of that name is the API managed identity (exported as API_IDENTITY_CLIENT_ID).
set -euo pipefail

keys=(
  API_URL
  WEB_URL
  API_IMAGE
  AZURE_RESOURCE_GROUP
  AZURE_CONTAINER_APP_NAME
  AZURE_CONTAINER_APPS_ENVIRONMENT_NAME
  AZURE_STATIC_WEB_APP_NAME
  FOUNDRY_PROJECT_ENDPOINT
  AI_SERVICES_ENDPOINT
  AZURE_AI_ACCOUNT_NAME
  AZURE_AI_PROJECT_NAME
  CHAT_MODEL
  NANO_MODEL
  EMBEDDING_MODEL
  SEARCH_ENDPOINT
  AZURE_SEARCH_SERVICE_NAME
  AZURE_SEARCH_RESOURCE_GROUP
  STORAGE_TABLE_ENDPOINT
  AZURE_STORAGE_ACCOUNT_NAME
  API_IDENTITY_CLIENT_ID
  AZURE_APP_INSIGHTS_NAME
  AZURE_LOG_ANALYTICS_WORKSPACE_NAME
  APPLICATIONINSIGHTS_CONNECTION_STRING
)

values_file="$(mktemp)"
azd env get-values --output json > "$values_file"

for key in "${keys[@]}"; do
  value="$(jq -r --arg k "$key" '.[$k] // empty' "$values_file")"
  if [ -z "$value" ]; then
    echo "::warning::azd output $key is empty"
    continue
  fi
  if [ "$key" = "APPLICATIONINSIGHTS_CONNECTION_STRING" ]; then
    echo "::add-mask::$value"
  fi
  {
    echo "$key<<__BENEFURA_EOF__"
    echo "$value"
    echo "__BENEFURA_EOF__"
  } >> "$GITHUB_ENV"
done

rm -f "$values_file"
echo "Exported ${#keys[@]} azd outputs to GITHUB_ENV."
