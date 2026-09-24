#!/usr/bin/env bash
# Spike 4: Container Apps Consumption ingress limits (8 MB uploads, 240 s timeout) via a throwaway app.
set -euo pipefail

: "${AZURE_RESOURCE_GROUP:?run azd env get-values first}"
: "${AZURE_CONTAINER_APPS_ENVIRONMENT_NAME:?run azd env get-values first}"
APP="${SPIKE_APP_NAME:-ca-spike-ingress}"
IMAGE="${SPIKE_IMAGE:-docker.io/library/python:3.12-slim}"
WORK="$(mktemp -d)"
RESULTS_DIR="$(cd "$(dirname "$0")" && pwd)/results"
mkdir -p "$RESULTS_DIR"

cleanup() {
  echo "Deleting ${APP}..."
  az containerapp delete -n "$APP" -g "$AZURE_RESOURCE_GROUP" --yes -o none 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

az config set extension.use_dynamic_install=yes_without_prompt --only-show-errors
az extension add --name containerapp --upgrade --yes --only-show-errors

env_id="$(az containerapp env show -n "$AZURE_CONTAINER_APPS_ENVIRONMENT_NAME" -g "$AZURE_RESOURCE_GROUP" --query id -o tsv)"
location="$(az containerapp env show -n "$AZURE_CONTAINER_APPS_ENVIRONMENT_NAME" -g "$AZURE_RESOURCE_GROUP" --query location -o tsv)"

server_py='import http.server, json, time, urllib.parse
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        n = int(self.headers.get("content-length", 0)); read = 0
        while read < n:
            read += len(self.rfile.read(min(65536, n - read)))
        s = int(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("sleep", ["0"])[0])
        time.sleep(s)
        body = json.dumps({"bytes": read, "slept": s}).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)
http.server.ThreadingHTTPServer(("", 8080), H).serve_forever()'

python3 - "$WORK/app.yaml" "$env_id" "$location" "$IMAGE" "$server_py" <<'PY'
import json, sys
path, env_id, location, image, server = sys.argv[1:]
spec = {
    "location": location,
    "properties": {
        "managedEnvironmentId": env_id,
        "workloadProfileName": "Consumption",
        "configuration": {"ingress": {"external": True, "targetPort": 8080, "transport": "auto"}},
        "template": {
            "containers": [{"name": "echo", "image": image, "command": ["python3", "-c", server],
                            "resources": {"cpu": 0.25, "memory": "0.5Gi"}}],
            "scale": {"minReplicas": 1, "maxReplicas": 1},
        },
    },
}
# JSON is valid YAML.
open(path, "w").write(json.dumps(spec))
PY

echo "Creating ${APP} (${IMAGE})..."
az containerapp create -n "$APP" -g "$AZURE_RESOURCE_GROUP" --yaml "$WORK/app.yaml" -o none
fqdn="$(az containerapp show -n "$APP" -g "$AZURE_RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)"
url="https://${fqdn}"
for _ in $(seq 1 30); do curl -fsS --max-time 10 "$url/" > /dev/null 2>&1 && break; sleep 5; done

upload() {
  head -c $(( $1 * 1024 * 1024 )) /dev/urandom > "$WORK/chunk.pdf"
  curl -sS -o "$WORK/upload.json" -w '%{http_code} %{time_total}' --max-time 300 \
    -F "region=CA" -F "pages=1,2,3,4,5" -F "file=@$WORK/chunk.pdf;type=application/pdf" "$url/upload" || true
}
wait_for() {
  curl -sS -o "$WORK/wait.json" -w '%{http_code} %{time_total}' --max-time 320 -X POST "$url/wait?sleep=$1" || true
}

read -r code8 t8 <<< "$(upload 8)";   bytes8="$(jq -r .bytes "$WORK/upload.json" 2>/dev/null || echo 0)"
read -r code12 t12 <<< "$(upload 12)"; bytes12="$(jq -r .bytes "$WORK/upload.json" 2>/dev/null || echo 0)"
read -r code200 t200 <<< "$(wait_for 200)"
read -r code250 t250 <<< "$(wait_for 250)"

echo "8 MB upload:  HTTP ${code8} in ${t8}s, server read ${bytes8} bytes"
echo "12 MB upload: HTTP ${code12} in ${t12}s, server read ${bytes12} bytes"
echo "200 s wait:   HTTP ${code200} after ${t200}s"
echo "250 s wait:   HTTP ${code250} after ${t250}s (expect a 504/timeout near 240 s)"

passed=false
if [ "$code8" = "200" ] && [ "$code200" = "200" ] && [ "$code250" != "200" ]; then passed=true; fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
jq -n --arg at "$stamp" --argjson passed "$passed" \
  --arg code8 "$code8" --arg t8 "$t8" --arg bytes8 "$bytes8" \
  --arg code12 "$code12" --arg t12 "$t12" --arg bytes12 "$bytes12" \
  --arg code200 "$code200" --arg t200 "$t200" --arg code250 "$code250" --arg t250 "$t250" \
  '{spike: "s04", at: $at, passed: $passed,
    upload8MB: {http: $code8, seconds: $t8, bytes: $bytes8},
    upload12MB: {http: $code12, seconds: $t12, bytes: $bytes12},
    wait200s: {http: $code200, seconds: $t200}, wait250s: {http: $code250, seconds: $t250}}' \
  > "$RESULTS_DIR/s04-${stamp}.json"
echo "$([ "$passed" = true ] && echo PASS || echo FAIL): s04 -> spikes/results/s04-${stamp}.json"
