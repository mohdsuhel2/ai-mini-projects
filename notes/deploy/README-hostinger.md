# Mapping notes.noobius.in on the existing VPS

This is the runbook for **your** setup, not a generic one. Here is what is
actually on the server today.

## What is already running on VM 1387290 (76.13.241.82)

| Project | Container | Port | What it is |
|---|---|---|---|
| `docforge` | `document-generator-web` | host `:80`, `:443` | **nginx.** Serves noobius.in, www, bill-receipt, and proxies to the groww gateway. |
| `groww-gateway` | `groww-gateway` | host `:8787` | autoIntraday's Groww proxy |
| `clickdesk` | app + postgres | `8080` | unrelated |
| `litra-word-puzzle` | api | `8083` | unrelated |

Port **8085 is free**, which is where Simply Notes will listen — bound to
`127.0.0.1`, so only nginx can reach it.

The important thing to understand: **`docforge` is the web server for the whole
domain.** It runs `nginx:1.27-alpine` in `network_mode: host`, clones this repo
on start, and serves `document-generator/deploy/nginx.conf`. Adding a subdomain
means editing that config, not standing up a second proxy.

## The three things that block a deploy right now

### 1. DNS is at GoDaddy, not Hostinger

```
$ dig +short NS noobius.in
ns27.domaincontrol.com.
ns28.domaincontrol.com.
```

The Hostinger DNS API returns an empty zone for `noobius.in`, so it cannot add
the record. **You have to add it**, in GoDaddy → noobius.in → DNS:

| Type | Name | Value | TTL |
|---|---|---|---|
| `A` | `notes` | `76.13.241.82` | 600 |

Verify before going further:

```bash
dig +short notes.noobius.in     # must print 76.13.241.82
```

Certbot proves domain control over HTTP, so nothing below can succeed until
this resolves.

### 2. `notes/` is not on GitHub

The VPS deploys by cloning `github.com/mohdsuhel2/ai-mini-projects`. Nothing in
`notes/` is committed yet, so there is nothing for it to build.

The repo also has ~3,650 unrelated dirty files in `StockAnalayze/` and
`autoIntraday/`, so this needs a scoped commit:

```bash
cd ~/ai-mini-projects
git add notes/ docker-compose.yaml README.md
git commit -m "feat(notes): Simply Notes — local-first planner and note taking"
git push origin main
```

Worth saying out loud: **this repo is public.** Simply Notes contains no
secrets — no API keys, no tokens, and no user data, since everything a visitor
writes stays in their own browser. But the push is public, so it is your call.

### 3. The certificate does not cover notes.noobius.in

```
$ openssl s_client -connect noobius.in:443 | openssl x509 -noout -ext subjectAltName
DNS:bill-receipt.noobius.in, DNS:noobius.in, DNS:www.noobius.in
```

This is the step with real risk, because the same nginx and the same
certificate serve your live main site. The entrypoint's current logic only
requests a certificate when none exists:

```sh
if [ ! -f "${CERT_DIR}/fullchain.pem" ]; then   # never true now
```

So adding `-d notes.noobius.in` there would silently do nothing. The
certificate has to be **expanded** instead, and the failure path has to leave
noobius.in on HTTPS rather than dropping the whole domain to plain HTTP.

## The plan, in order

1. **You** add the GoDaddy A record and confirm it resolves.
2. **You** approve committing and pushing `notes/`.
3. Deploy the container — no risk to anything already running:
   ```bash
   ./deploy/deploy-hostinger.sh
   ```
   It refuses to run if `notes/` is uncommitted or unpushed, so it can never
   deploy a version the VPS cannot see.
4. Verify it is up, from the VPS's own perspective, before touching nginx.
5. Add the `notes.noobius.in` server block and expand the certificate — the
   only step that touches the live proxy, done with an explicit rollback path.

Steps 1–4 cannot affect noobius.in. Step 5 can, which is why it is last and
separate.

## Step 5 in detail

Two edits to `document-generator/deploy/`, then a `docforge` redeploy.

**`nginx.conf`** — append a server block:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name notes.noobius.in;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    http2 on;
    server_name notes.noobius.in;

    ssl_certificate     /etc/letsencrypt/live/noobius.in/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/noobius.in/privkey.pem;

    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    gzip on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    # Build assets are content-hashed and safe to cache forever.
    location /_next/static/ {
        proxy_pass http://127.0.0.1:8085;
        proxy_set_header Host $host;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # The service worker must never be cached, or updates cannot ship.
    location = /sw.js {
        proxy_pass http://127.0.0.1:8085;
        proxy_set_header Host $host;
        add_header Cache-Control "public, max-age=0, must-revalidate";
    }

    location / {
        proxy_pass http://127.0.0.1:8085;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**`docker-entrypoint.sh`** — expand the certificate when a domain is missing,
and never downgrade the site if that fails:

```sh
DOMAINS="-d noobius.in -d www.noobius.in -d bill-receipt.noobius.in -d notes.noobius.in"

# Expand an existing certificate to cover any newly added subdomain. A failure
# here must leave the current certificate in place: noobius.in staying on
# HTTPS matters more than notes.noobius.in coming up today.
if [ -f "${CERT_DIR}/fullchain.pem" ] && \
   ! openssl x509 -in "${CERT_DIR}/fullchain.pem" -noout -text | grep -q "notes.noobius.in"; then
  echo "Expanding certificate to cover notes.noobius.in..."
  use_http_config
  start_nginx_bg
  certbot certonly --webroot -w "$WEBROOT" --expand $DOMAINS \
    --email "$CERT_EMAIL" --agree-tos --non-interactive --no-eff-email \
    || echo "Expand failed; keeping the existing certificate."
  stop_nginx
fi
```

Then redeploy docforge so it re-clones and restarts.

**Rollback**: revert the two files, push, redeploy docforge. The certificate
itself is additive — expanding it never invalidates the existing names.

## Verifying

```bash
curl -I https://notes.noobius.in
curl -s https://notes.noobius.in/robots.txt
curl -I https://noobius.in            # the main site must still be fine
echo | openssl s_client -connect notes.noobius.in:443 -servername notes.noobius.in 2>/dev/null \
  | openssl x509 -noout -ext subjectAltName
```

In a browser: it should land on the **Notes** tab with `?tab=notes` in the
address bar, a task added under Todos should survive a refresh, and "Install
app" should be offered.

## Operating it afterwards

Redeploying Simply Notes is just `./deploy/deploy-hostinger.sh` after pushing.
It rebuilds on the VPS and restarts the container; nothing else is touched, and
there is no data to migrate — every visitor's notes live in their own browser.
