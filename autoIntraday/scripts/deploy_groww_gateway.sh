#!/usr/bin/env bash
# Deploy groww-gateway to Hostinger VPS (hPanel → Docker Manager works too).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_ID=1387290
PROJECT=groww-gateway

if [[ -z "${HOSTINGER_API_TOKEN:-}" ]]; then
  HOSTINGER_API_TOKEN="$(python3 -c "import json; print(json.load(open('$HOME/.cursor/mcp.json'))['mcpServers']['hostinger-api']['env']['HOSTINGER_API_TOKEN'])")"
  export HOSTINGER_API_TOKEN
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

TOKEN_FILE="$ROOT/.groww-gateway-token.local"
if [[ ! -f "$TOKEN_FILE" ]]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi
export GROWW_GATEWAY_TOKEN="$(tr -d '\n' < "$TOKEN_FILE")"

: "${GROWW_API_KEY:?Set GROWW_API_KEY in autoIntraday/.env}"
: "${GROWW_TOTP_SECRET:?Set GROWW_TOTP_SECRET in autoIntraday/.env}"

python3 - "$ROOT" "$VM_ID" "$PROJECT" <<'PY'
import json, os, sys, urllib.request
from pathlib import Path

root, vm_id, project = sys.argv[1:4]
compose = (Path(root) / "groww_gateway/deploy/docker-compose.vps.yml").read_text()
payload = {
    "project_name": project,
    "content": compose,
    "environment": json.dumps({
        "GROWW_API_KEY": os.environ["GROWW_API_KEY"],
        "GROWW_TOTP_SECRET": os.environ["GROWW_TOTP_SECRET"],
        "GROWW_GATEWAY_TOKEN": os.environ["GROWW_GATEWAY_TOKEN"],
    }),
}
req = urllib.request.Request(
    f"https://developers.hostinger.com/api/vps/v1/virtual-machines/{vm_id}/docker",
    data=json.dumps(payload).encode(),
    headers={
        "Authorization": f"Bearer {os.environ['HOSTINGER_API_TOKEN']}",
        "Content-Type": "application/json",
        # Hostinger sits behind Cloudflare, which rejects urllib's default UA with a 403
        # (error 1010). Without this the deploy fails before it reaches the API at all.
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=180) as resp:
    print("deployed", resp.status)
    print(resp.read().decode()[:800])
PY

echo ""
echo "Gateway token saved in: $TOKEN_FILE"
echo "Add to Mac .env:"
echo "  GROWW_GATEWAY_URL=http://76.13.241.82:8787"
echo "  GROWW_GATEWAY_TOKEN=<contents of .groww-gateway-token.local>"
echo "Health check: curl http://76.13.241.82:8787/health"
