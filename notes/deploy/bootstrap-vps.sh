#!/usr/bin/env bash
#
# One-time setup on a fresh Hostinger VPS (Ubuntu 22.04 / 24.04).
# Run this ON the server, as root:
#
#   bash bootstrap-vps.sh notes.noobius.in you@example.com
#
# Installs Docker, nginx and certbot, opens the firewall, and puts the
# reverse-proxy config in place. It does NOT deploy the app — run
# deploy/deploy.sh from your machine afterwards.

set -euo pipefail

DOMAIN="${1:-notes.noobius.in}"
EMAIL="${2:-}"
HOST_PORT="${HOST_PORT:-8085}"

if [[ -z "$EMAIL" ]]; then
  echo "usage: bash bootstrap-vps.sh <domain> <email-for-letsencrypt>" >&2
  exit 64
fi

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg nginx

echo "==> Docker"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

echo "==> Firewall"
if command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  ufw allow 'Nginx Full' >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
fi

echo "==> nginx (HTTP only for now, so certbot can complete the challenge)"
mkdir -p /var/www/certbot
cat > "/etc/nginx/sites-available/$DOMAIN" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ { root /var/www/certbot; }

    location / {
        proxy_pass http://127.0.0.1:$HOST_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX

ln -sf "/etc/nginx/sites-available/$DOMAIN" "/etc/nginx/sites-enabled/$DOMAIN"
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> TLS"
apt-get install -y -qq certbot python3-certbot-nginx
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
systemctl enable --now certbot.timer

echo
echo "Server is ready for $DOMAIN."
echo "Now run, from your laptop:  ./deploy/deploy.sh root@<this-server-ip>"
