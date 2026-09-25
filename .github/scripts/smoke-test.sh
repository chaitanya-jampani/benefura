#!/usr/bin/env bash
# Locally: `azd env get-values > .env && set -a && . ./.env && set +a`, with curl, jq, openssl and a logged-in az.
set -euo pipefail

: "${API_URL:?API_URL is required}"
: "${WEB_URL:?WEB_URL is required}"
: "${AZURE_RESOURCE_GROUP:?AZURE_RESOURCE_GROUP is required}"
: "${AZURE_APP_INSIGHTS_NAME:?AZURE_APP_INSIGHTS_NAME is required}"

api="${API_URL%/}"
web="${WEB_URL%/}"
api_origin="$(printf '%s' "$api" | sed -E 's#^(https?://[^/]+).*#\1#')"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

summary() { if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then echo "$*" >> "$GITHUB_STEP_SUMMARY"; fi; }
pass() { echo "PASS: $*"; summary "- :white_check_mark: $*"; }
fail() { echo "::error::FAIL: $*"; summary "- :x: $*"; exit 1; }
# Last occurrence wins: curl -D also records redirect and 100-continue header blocks.
header() { { grep -i "^$1:" "$2" || true; } | tail -n 1 | cut -d: -f2- | tr -d '\r' | sed -E 's/^[[:space:]]+//'; }

summary "### Smoke tests"
summary ""

trace_id="$(openssl rand -hex 16)"
span_id="$(openssl rand -hex 8)"
code=000
for attempt in $(seq 1 15); do
  code="$(curl -sS -o "$tmp/healthz.json" -D "$tmp/healthz.headers" -w '%{http_code}' --max-time 60 \
    -H "traceparent: 00-${trace_id}-${span_id}-01" \
    -H "Origin: ${web}" \
    "${api}/healthz" || true)"
  if [ "$code" = "200" ]; then break; fi
  echo "healthz attempt ${attempt}: HTTP ${code}; retrying in 10 s (cold start)"
  sleep 10
done
[ "$code" = "200" ] || fail "/healthz returned HTTP ${code}"
jq -e '.ok == true' "$tmp/healthz.json" > /dev/null || fail "/healthz body does not have ok=true: $(cat "$tmp/healthz.json")"
pass "/healthz 200 $(jq -c '{aiEnabled, aiMode, budgetRemainingPct, agentVersions}' "$tmp/healthz.json")"

allow_origin="$(header access-control-allow-origin "$tmp/healthz.headers")"
[ "$allow_origin" = "$web" ] || fail "GET /healthz from ${web}: access-control-allow-origin is '${allow_origin}'"
expose="$(header access-control-expose-headers "$tmp/healthz.headers")"
printf '%s' "$expose" | grep -qi 'x-benefura-trace-id' \
  || fail "access-control-expose-headers ('${expose}') does not include x-benefura-trace-id"
returned_trace="$(header x-benefura-trace-id "$tmp/healthz.headers")"
[ -n "$returned_trace" ] || echo "::warning::/healthz did not return x-benefura-trace-id; using the traceparent id"
pass "CORS actual request from ${web} exposes x-benefura-trace-id"

code="$(curl -sS -o /dev/null -D "$tmp/preflight.headers" -w '%{http_code}' --max-time 60 -X OPTIONS \
  -H "Origin: ${web}" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  "${api}/api/chat" || true)"
[ "$code" = "200" ] || fail "CORS preflight POST /api/chat returned HTTP ${code}"
[ "$(header access-control-allow-origin "$tmp/preflight.headers")" = "$web" ] \
  || fail "CORS preflight does not allow ${web}"
header access-control-allow-methods "$tmp/preflight.headers" | grep -q POST \
  || fail "CORS preflight does not allow POST"
pass "CORS preflight POST /api/chat from ${web}"

curl -sS -o /dev/null -D "$tmp/evil.headers" --max-time 60 -X OPTIONS \
  -H "Origin: https://not-benefura.example" \
  -H "Access-Control-Request-Method: POST" \
  "${api}/api/chat" || true
if [ "$(header access-control-allow-origin "$tmp/evil.headers")" = "https://not-benefura.example" ]; then
  fail "CORS allows a foreign origin"
fi
pass "CORS rejects a foreign origin"

csp=""
for attempt in $(seq 1 10); do
  code="$(curl -sS -o /dev/null -D "$tmp/web.headers" -w '%{http_code}' --max-time 30 "${web}/" || true)"
  csp="$(header content-security-policy "$tmp/web.headers")"
  if [ "$code" = "200" ] && [ -n "$csp" ]; then break; fi
  echo "web attempt ${attempt}: HTTP ${code}, CSP '${csp}'; retrying in 15 s"
  sleep 15
done
[ "$code" = "200" ] || fail "${web}/ returned HTTP ${code}"
[ -n "$csp" ] || fail "${web}/ has no Content-Security-Policy header"
printf '%s' "$csp" | grep -qF "connect-src 'self' ${api_origin}" || fail "CSP connect-src is not limited to self + ${api_origin}: ${csp}"
printf '%s' "$csp" | grep -qF "frame-ancestors 'none'" || fail "CSP lacks frame-ancestors 'none'"
printf '%s' "$csp" | grep -qF "object-src 'none'" || fail "CSP lacks object-src 'none'"
[ "$(header x-content-type-options "$tmp/web.headers")" = "nosniff" ] || fail "X-Content-Type-Options is not nosniff"
[ "$(header referrer-policy "$tmp/web.headers")" = "no-referrer" ] || fail "Referrer-Policy is not no-referrer"
pass "SWA serves CSP (connect-src 'self' ${api_origin}) and security headers"

trace="${returned_trace:-$trace_id}"
query="union requests, dependencies, traces | where operation_Id == '${trace}' | summarize n = count()"
found=0
for attempt in $(seq 1 20); do
  n="$(az monitor app-insights query \
    --app "$AZURE_APP_INSIGHTS_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --analytics-query "$query" \
    --offset 1h \
    --query 'tables[0].rows[0][0]' -o tsv 2> "$tmp/ai.err" || true)"
  if [ -n "$n" ] && [ "$n" != "0" ]; then
    found="$n"
    break
  fi
  echo "App Insights attempt ${attempt}: trace ${trace} not ingested yet; retrying in 30 s $(head -c 200 "$tmp/ai.err")"
  sleep 30
done
[ "$found" != "0" ] || fail "trace ${trace} not visible in Application Insights after 10 minutes"
pass "trace ${trace} visible in Application Insights (${found} telemetry rows)"
