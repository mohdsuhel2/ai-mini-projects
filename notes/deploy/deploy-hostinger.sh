#!/usr/bin/env bash
#
# Deploy Simply Notes to the Hostinger VPS through the Docker Manager API.
# Mirrors autoIntraday/scripts/deploy_groww_gateway.sh.
#
#   ./deploy/deploy-hostinger.sh
#
# The VPS builds from GitHub, so `notes/` must be committed and pushed first.
# This script refuses to run if the local tree is ahead of the remote, because
# a silent no-op deploy is worse than an error.

set -euo pipefail

VM_ID="${VM_ID:-1387290}"
PROJECT="${PROJECT:-simply-notes}"
VPS_IP="${VPS_IP:-76.13.241.82}"
SITE_URL="${NEXT_PUBLIC_SITE_URL:-https://notes.noobius.in}"
GA_ID="${NEXT_PUBLIC_GA_MEASUREMENT_ID:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"

if [[ -z "${HOSTINGER_API_TOKEN:-}" ]]; then
  HOSTINGER_API_TOKEN="$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.cursor/mcp.json')))['mcpServers']['hostinger-api']['env']['HOSTINGER_API_TOKEN'])")"
  export HOSTINGER_API_TOKEN
fi

echo "==> Local checks"
( cd "$ROOT" && npm run lint >/dev/null && npx tsc --noEmit && npm test --silent >/dev/null )
echo "    lint, types and tests pass"

echo "==> Confirming the VPS can actually see this code"
cd "$REPO_ROOT"
if [[ -n "$(git status --porcelain -- notes/)" ]]; then
  echo "!!  notes/ has uncommitted changes. The VPS builds from GitHub, so they" >&2
  echo "    would not be deployed. Commit and push notes/ first." >&2
  exit 1
fi
git fetch --quiet origin main
if [[ -n "$(git log --oneline origin/main..HEAD -- notes/)" ]]; then
  echo "!!  notes/ has commits that are not pushed to origin/main." >&2
  echo "    Push them, or the VPS will build the previous version." >&2
  exit 1
fi
echo "    notes/ is committed and pushed"

echo "==> Deploying project '$PROJECT' to VM $VM_ID"
python3 - "$ROOT" "$VM_ID" "$PROJECT" "$SITE_URL" "$GA_ID" <<'PY'
import json, os, sys, urllib.request, urllib.error
from pathlib import Path

root, vm_id, project, site_url, ga_id = sys.argv[1:6]
compose = (Path(root) / "deploy/docker-compose.hostinger.yml").read_text()

payload = {
    "project_name": project,
    "content": compose,
    # The API wants a dotenv blob, not JSON — it validates line by line and
    # rejects anything that is not KEY=value.
    "environment": "\n".join([
        f"NEXT_PUBLIC_SITE_URL={site_url}",
        f"NEXT_PUBLIC_GA_MEASUREMENT_ID={ga_id}",
    ]),
}
req = urllib.request.Request(
    f"https://developers.hostinger.com/api/vps/v1/virtual-machines/{vm_id}/docker",
    data=json.dumps(payload).encode(),
    headers={
        "Authorization": f"Bearer {os.environ['HOSTINGER_API_TOKEN']}",
        "Content-Type": "application/json",
        # Hostinger sits behind Cloudflare, which rejects urllib's default UA
        # with a 403 (error 1010) before the request reaches the API.
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "application/json",
    },
    method="POST",
)
try:
    # A cold Next.js build on a 2-vCPU box takes a few minutes.
    with urllib.request.urlopen(req, timeout=900) as resp:
        print("   ", resp.status, resp.read().decode()[:400])
except urllib.error.HTTPError as e:
    print("   HTTP", e.code, e.read().decode()[:600], file=sys.stderr)
    raise SystemExit(1)
PY

echo
echo "Deployed. Check it is answering behind the proxy:"
echo "  curl -I https://notes.noobius.in"
echo
echo "If notes.noobius.in is not mapped yet, see deploy/README-hostinger.md"
