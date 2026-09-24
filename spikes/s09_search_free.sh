#!/usr/bin/env bash
# Spike 9: find or create the subscription's single Free search service, then check Entra-only data-plane access.
set -euo pipefail

CREATE=false
LOCATION="canadacentral"
while [ $# -gt 0 ]; do
  case "$1" in
    --create) CREATE=true ;;
    --location) LOCATION="$2"; shift ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done

API="2026-04-01"   # data-plane version used by azure-search-documents 12.0
MGMT_API="2025-05-01"
RESULTS_DIR="$(cd "$(dirname "$0")" && pwd)/results"
mkdir -p "$RESULTS_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
sub="$(az account show --query id -o tsv)"

echo "== 1. Search services in subscription ${sub}"
services="$(az resource list --resource-type Microsoft.Search/searchServices \
  --query "[].{name:name, resourceGroup:resourceGroup, location:location, id:id}" -o json)"
echo "$services" | jq -r '.[] | "\(.name)\t\(.resourceGroup)\t\(.location)"'
free_name=""
free_rg=""
for id in $(echo "$services" | jq -r '.[].id'); do
  svc="$(az resource show --ids "$id" --api-version "$MGMT_API" -o json)"
  sku="$(echo "$svc" | jq -r '.sku.name')"
  echo "  $(echo "$svc" | jq -c '{name, location, sku: .sku.name, disableLocalAuth: .properties.disableLocalAuth,
    authOptions: .properties.authOptions, semanticSearch: .properties.semanticSearch}')"
  if [ "$sku" = "free" ]; then
    free_name="$(echo "$svc" | jq -r .name)"
    free_rg="$(echo "$id" | awk -F/ '{print $5}')"
  fi
done

created=false
if [ -n "$free_name" ]; then
  echo "Existing Free service: ${free_name} (${free_rg}). Reuse it:"
  echo "  azd env set AZURE_EXISTING_SEARCH_SERVICE_NAME ${free_name}"
  echo "  azd env set AZURE_EXISTING_SEARCH_RESOURCE_GROUP ${free_rg}"
elif [ "$CREATE" = true ]; then
  free_rg="rg-benefura-search"
  free_name="srch-benefura-$(openssl rand -hex 3)"
  echo "== 2. Creating Free service ${free_name} in ${LOCATION}"
  az group create -n "$free_rg" -l "$LOCATION" -o none
  az search service create -n "$free_name" -g "$free_rg" -l "$LOCATION" --sku free \
    --disable-local-auth true --semantic-search free -o none
  created=true
else
  echo "No Free service found. Re-run with --create, or let azd create one in ${LOCATION} (searchLocation)."
  jq -n --arg at "$stamp" '{spike: "s09", at: $at, passed: null, existingFree: null}' > "$RESULTS_DIR/s09-${stamp}.json"
  exit 0
fi

svc="$(az search service show -n "$free_name" -g "$free_rg" -o json)"
endpoint="https://${free_name}.search.windows.net"
local_auth="$(echo "$svc" | jq -r '.disableLocalAuth')"
semantic="$(echo "$svc" | jq -r '.semanticSearch')"
auth_options="$(echo "$svc" | jq -c '.authOptions')"
echo "disableLocalAuth=${local_auth} semanticSearch=${semantic} authOptions=${auth_options}"
if [ "$local_auth" != "true" ] && { [ "$auth_options" = "null" ] || [ "$auth_options" = '{"apiKeyOnly":{}}' ]; }; then
  echo "RBAC is not enabled for the data plane. Enable it (keeps keys working for other apps):"
  echo "  az search service update -n ${free_name} -g ${free_rg} --auth-options aadOrApiKey --aad-auth-failure-mode http401WithBearerChallenge"
fi

echo "== 3. RBAC data-plane check"
me="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || az account show --query user.name -o tsv)"
scope="$(echo "$svc" | jq -r .id)"
for role in "Search Service Contributor" "Search Index Data Contributor"; do
  az role assignment create --assignee "$me" --role "$role" --scope "$scope" -o none 2>/dev/null || true
done
echo "Waiting 60 s for role propagation..."
sleep 60
token="$(az account get-access-token --resource https://search.azure.com --query accessToken -o tsv)"
index="spike-s09-$(date +%s)"
auth=(-H "Authorization: Bearer ${token}" -H "Content-Type: application/json")

index_body="$(jq -n --arg name "$index" '{
  name: $name,
  fields: [
    {name: "id", type: "Edm.String", key: true, filterable: true},
    {name: "content", type: "Edm.String", searchable: true},
    {name: "region", type: "Edm.String", filterable: true},
    {name: "contentVector", type: "Collection(Edm.Single)", searchable: true, dimensions: 4, vectorSearchProfile: "hnsw"}
  ],
  vectorSearch: {algorithms: [{name: "hnsw-alg", kind: "hnsw"}], profiles: [{name: "hnsw", algorithm: "hnsw-alg"}]},
  semantic: {configurations: [{name: "default", prioritizedFields: {prioritizedContentFields: [{fieldName: "content"}]}}]}
}')"
create_code="$(curl -sS -o /dev/null -w '%{http_code}' -X PUT "${endpoint}/indexes/${index}?api-version=${API}" "${auth[@]}" -d "$index_body")"
upload_code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${endpoint}/indexes/${index}/docs/index?api-version=${API}" "${auth[@]}" \
  -d '{"value":[{"@search.action":"upload","id":"1","region":"CA","content":"Massage therapy may qualify as a medical expense when prescribed.","contentVector":[0.1,0.2,0.3,0.4]}]}')"
sleep 3
query_code="$(curl -sS -o "$RESULTS_DIR/s09-query-${stamp}.json" -w '%{http_code}' -X POST "${endpoint}/indexes/${index}/docs/search?api-version=${API}" "${auth[@]}" \
  -d '{"search":"is massage a medical expense","queryType":"semantic","semanticConfiguration":"default","filter":"region eq '"'"'CA'"'"'","vectorQueries":[{"kind":"vector","vector":[0.1,0.2,0.3,0.4],"fields":"contentVector","k":3}],"top":3}')"
reranker="$(jq -r '.value[0]["@search.rerankerScore"] // empty' "$RESULTS_DIR/s09-query-${stamp}.json" 2>/dev/null || true)"
key_code="$(curl -sS -o /dev/null -w '%{http_code}' "${endpoint}/indexes?api-version=${API}" -H "api-key: not-a-real-key")"
curl -sS -o /dev/null -X DELETE "${endpoint}/indexes/${index}?api-version=${API}" "${auth[@]}" || true
rm -f "$RESULTS_DIR/s09-query-${stamp}.json"

echo "create index: ${create_code}, upload: ${upload_code}, semantic+vector query: ${query_code} (rerankerScore=${reranker:-none}), api-key request: ${key_code}"
passed=false
if [ "$create_code" = "201" ] && [ "$query_code" = "200" ] && [ -n "$reranker" ] && [ "$key_code" != "200" ]; then passed=true; fi
jq -n --arg at "$stamp" --argjson passed "$passed" --arg name "$free_name" --arg rg "$free_rg" \
  --arg location "$(echo "$svc" | jq -r .location)" --argjson created "$created" --arg semantic "$semantic" \
  --arg localAuthDisabled "$local_auth" --arg create "$create_code" --arg upload "$upload_code" --arg query "$query_code" \
  --arg reranker "${reranker:-}" --arg key "$key_code" \
  '{spike: "s09", at: $at, passed: $passed, service: {name: $name, resourceGroup: $rg, location: $location, created: $created,
    semanticSearch: $semantic, disableLocalAuth: $localAuthDisabled},
    dataPlane: {createIndex: $create, upload: $upload, semanticVectorQuery: $query, rerankerScore: $reranker, apiKeyRequest: $key}}' \
  > "$RESULTS_DIR/s09-${stamp}.json"
echo "$([ "$passed" = true ] && echo PASS || echo FAIL): s09 -> spikes/results/s09-${stamp}.json"
