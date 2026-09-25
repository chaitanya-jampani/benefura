#!/usr/bin/env bash
# Spike 8: GlobalStandard quota and model versions for the three deployments and chat fallbacks (read-only).
set -euo pipefail

LOCATION="${1:-${AZURE_LOCATION:-eastus2}}"
SKU="${DEPLOYMENT_SKU:-GlobalStandard}"
RESULTS_DIR="$(cd "$(dirname "$0")" && pwd)/results"
mkdir -p "$RESULTS_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$RESULTS_DIR/s08-${stamp}.json"

echo "Subscription: $(az account show --query '{name:name, id:id}' -o tsv)"
echo "Location: ${LOCATION}, SKU: ${SKU}"

usage_json="$(az cognitiveservices usage list --location "$LOCATION" -o json)"
models_json="$(az cognitiveservices model list --location "$LOCATION" -o json)"

check_model() { # check_model <model> <required K TPM>
  local model="$1" need="$2" name limit current free versions
  name="OpenAI.${SKU}.${model}"
  limit="$(jq -r --arg n "$name" '[.[] | select(.name.value == $n)][0].limit // 0' <<< "$usage_json")"
  current="$(jq -r --arg n "$name" '[.[] | select(.name.value == $n)][0].currentValue // 0' <<< "$usage_json")"
  free="$(awk -v l="$limit" -v c="$current" 'BEGIN { printf "%d", l - c }')"
  versions="$(jq -c --arg m "$model" --arg sku "$SKU" \
    '[.[] | select(.model.name == $m) | select([.model.skus[]?.name] | index($sku)) | .model.version] | unique' <<< "$models_json")"
  local ok=false
  if [ "$free" -ge "$need" ] && [ "$versions" != "[]" ]; then ok=true; fi
  printf '%-26s limit=%-6s used=%-6s free=%-6s need=%-4s versions=%s %s\n' >&2 \
    "$model" "$limit" "$current" "$free" "$need" "$versions" "$([ "$ok" = true ] && echo ok || echo SHORT)"
  jq -n --arg model "$model" --argjson limit "${limit%.*}" --argjson current "${current%.*}" --argjson free "$free" \
    --argjson need "$need" --argjson versions "$versions" --argjson ok "$ok" \
    '{model: $model, limitK: $limit, usedK: $current, freeK: $free, needK: $need, versions: $versions, ok: $ok}'
}

rows=()
for spec in gpt-5-mini:200 gpt-5-nano:100 text-embedding-3-small:100 gpt-5.4-mini:200 gpt-4.1-mini:200; do
  [ "$spec" = "gpt-5.4-mini:200" ] && echo "Fallbacks for the chat model:" >&2
  rows+=("$(check_model "${spec%%:*}" "${spec##*:}")")
done

printf '%s\n' "${rows[@]}" | jq -s --arg at "$stamp" --arg location "$LOCATION" --arg sku "$SKU" \
  '{spike: "s08", at: $at, location: $location, sku: $sku, models: .,
    passed: ([.[0:3][] | .ok] | all)}' > "$out"
echo "$(jq -r 'if .passed then "PASS" else "FAIL" end' "$out"): s08 -> spikes/results/$(basename "$out")"
echo "If the chat model is short: request quota, lower AZURE_CHAT_MODEL_CAPACITY, or set AZURE_CHAT_MODEL_NAME/VERSION to a fallback."
