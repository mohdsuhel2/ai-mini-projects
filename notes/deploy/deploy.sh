#!/usr/bin/env bash
#
# Deploy Simply Notes to a VPS over SSH.
#
#   ./deploy/deploy.sh root@203.0.113.10
#
# Copies the source, builds the image on the server and restarts the container.
# The build happens on the VPS so no registry is needed; the multi-stage
# Dockerfile means only the runtime layer survives.
#
# Environment:
#   NEXT_PUBLIC_SITE_URL           default https://notes.noobius.in
#   NEXT_PUBLIC_GA_MEASUREMENT_ID  optional; unset means analytics never loads
#   REMOTE_DIR                     default /opt/simply-notes
#   HOST_PORT                      default 8085

set -euo pipefail

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "usage: $0 user@host" >&2
  exit 64
fi

SITE_URL="${NEXT_PUBLIC_SITE_URL:-https://notes.noobius.in}"
GA_ID="${NEXT_PUBLIC_GA_MEASUREMENT_ID:-}"
REMOTE_DIR="${REMOTE_DIR:-/opt/simply-notes}"
HOST_PORT="${HOST_PORT:-8085}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Checking the build locally before touching the server"
( cd "$HERE" && npm run lint >/dev/null && npx tsc --noEmit && npm test --silent >/dev/null )
echo "    lint, types and tests pass"

echo "==> Copying source to $TARGET:$REMOTE_DIR"
ssh "$TARGET" "mkdir -p '$REMOTE_DIR'"
# node_modules and .next are rebuilt on the server; sending them wastes minutes
# and risks shipping macOS-native binaries to a Linux host.
rsync -az --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude '.next' \
  --exclude 'coverage' \
  --exclude '.env*' \
  "$HERE/" "$TARGET:$REMOTE_DIR/"

echo "==> Building and restarting on the server"
ssh "$TARGET" bash -s <<REMOTE
set -euo pipefail
cd "$REMOTE_DIR"

docker build \
  --build-arg NEXT_PUBLIC_SITE_URL="$SITE_URL" \
  --build-arg NEXT_PUBLIC_GA_MEASUREMENT_ID="$GA_ID" \
  -f deploy/Dockerfile \
  -t simply-notes:latest .

docker rm -f simply-notes-web >/dev/null 2>&1 || true
docker run -d \
  --name simply-notes-web \
  --restart unless-stopped \
  -p 127.0.0.1:$HOST_PORT:3000 \
  simply-notes:latest

docker image prune -f >/dev/null 2>&1 || true
REMOTE

echo "==> Waiting for the container to report healthy"
status=starting
for _ in $(seq 1 30); do
  status=$(ssh "$TARGET" "docker inspect --format '{{.State.Health.Status}}' simply-notes-web 2>/dev/null" || echo starting)
  [[ "$status" == "healthy" ]] && break
  sleep 2
done
if [[ "$status" != "healthy" ]]; then
  echo "!!  Container is '$status', not healthy. Logs:" >&2
  ssh "$TARGET" "docker logs --tail 40 simply-notes-web" >&2 || true
  exit 1
fi

echo "==> Verifying from the server itself"
ssh "$TARGET" "curl -fsS -o /dev/null -w 'local  HTTP %{http_code}\n' http://127.0.0.1:$HOST_PORT/"

echo
echo "Deployed. If nginx is already configured, check $SITE_URL"
