#!/bin/sh
set -e

DOMAIN="noobius.in"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
WEBROOT="/var/www/certbot"
CERT_EMAIL="${CERT_EMAIL:-admin@noobius.in}"
DEPLOY_DIR="/tmp/r/document-generator"

mkdir -p "$WEBROOT" /etc/letsencrypt

systemctl stop nginx 2>/dev/null || service nginx stop 2>/dev/null || true
lsof -ti:80 | xargs -r kill -9 2>/dev/null || true
lsof -ti:443 | xargs -r kill -9 2>/dev/null || true
sleep 1

rm -rf /usr/share/nginx/html/*
cp -r "${DEPLOY_DIR}/." /usr/share/nginx/html/

use_http_config() {
  cp "${DEPLOY_DIR}/deploy/nginx-http.conf" /etc/nginx/conf.d/default.conf
}

use_ssl_config() {
  cp "${DEPLOY_DIR}/deploy/nginx.conf" /etc/nginx/conf.d/default.conf
}

start_nginx_bg() {
  nginx -t
  nginx
}

stop_nginx() {
  nginx -s stop 2>/dev/null || true
  sleep 1
}

if [ -f "${CERT_DIR}/fullchain.pem" ]; then
  certbot renew --quiet --webroot -w "$WEBROOT" || true
fi

if [ ! -f "${CERT_DIR}/fullchain.pem" ]; then
  echo "Requesting Let's Encrypt certificate for ${DOMAIN}..."
  use_http_config
  start_nginx_bg
  if certbot certonly --webroot -w "$WEBROOT" \
    -d noobius.in \
    -d www.noobius.in \
    -d bill-receipt.noobius.in \
    -d notes.noobius.in \
    --email "$CERT_EMAIL" \
    --agree-tos \
    --non-interactive \
    --no-eff-email; then
    echo "Certificate issued successfully."
  else
    echo "Certificate request failed; serving HTTP only."
    stop_nginx
    use_http_config
    exec nginx -g 'daemon off;'
  fi
  stop_nginx
fi

if [ -f "${CERT_DIR}/fullchain.pem" ]; then
  use_ssl_config
else
  use_http_config
fi

# Add any name the certificate does not already carry.
#
# The issue branch above only runs when there is no certificate at all, so a
# new subdomain added later would otherwise never reach the certificate. nginx
# is started with the real config here, and every :80 block answers the ACME
# challenge from the shared webroot, so no config switch and no downtime.
#
# A failure must leave the existing certificate untouched: noobius.in staying
# on HTTPS matters far more than a new subdomain arriving today. The most
# likely cause of failure is DNS for the new name not being live yet, and the
# next restart simply tries again.
if [ -f "${CERT_DIR}/fullchain.pem" ] \
   && ! openssl x509 -in "${CERT_DIR}/fullchain.pem" -noout -text \
        | grep -q "DNS:notes.noobius.in"; then
  echo "Certificate does not cover notes.noobius.in; attempting to expand..."
  start_nginx_bg
  if certbot certonly --webroot -w "$WEBROOT" --expand \
    -d noobius.in \
    -d www.noobius.in \
    -d bill-receipt.noobius.in \
    -d notes.noobius.in \
    --email "$CERT_EMAIL" \
    --agree-tos \
    --non-interactive \
    --no-eff-email; then
    echo "Certificate expanded to cover notes.noobius.in."
  else
    echo "Expand failed. Keeping the existing certificate; noobius.in is unaffected."
    echo "Most likely DNS for notes.noobius.in is not live yet. It retries on next restart."
  fi
  stop_nginx
fi

exec nginx -g 'daemon off;'
