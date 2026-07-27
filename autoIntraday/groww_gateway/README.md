# Groww Gateway (VPS static-IP relay)

Groww’s trade API only accepts requests from **whitelisted static IPs**. This service runs on your Hostinger VPS and proxies all Groww calls so **autoIntraday on your Mac** (any WiFi) can trade without a home static IP.

```
Mac (autoIntraday)  --Bearer token-->  VPS 76.13.241.82:8787  --Groww SDK-->  api.groww.in
```

## Groww dashboard — IP to whitelist

Configure this IP at Groww:

**`76.13.241.82`**

(Your Hostinger VPS IPv4 — `srv1387290.hstgr.cloud`)

---

## 1. VPS setup (one-time)

### Generate a gateway token (keep secret)

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Deploy on Hostinger

Set these environment variables on the `groww-gateway` Docker project:

| Variable | Where | Example |
|----------|--------|---------|
| `GROWW_API_KEY` | VPS only | from Groww developer settings |
| `GROWW_TOTP_SECRET` | VPS only | TOTP secret from Groww |
| `GROWW_GATEWAY_TOKEN` | VPS + Mac | random string from above |

Compose file: `groww_gateway/deploy/docker-compose.vps.yml`

### Open firewall port 8787

In Hostinger hPanel → VPS → **Firewall**, allow **TCP 8787** (or restrict to your IPs if you prefer).

### Health check

```bash
curl http://76.13.241.82:8787/health
# {"status":"ok"}

curl -H "Authorization: Bearer YOUR_GATEWAY_TOKEN" http://76.13.241.82:8787/v1/ready
# {"status":"ready","mode":"live"}
```

---

## 2. Mac setup (autoIntraday)

Add to `~/.autointraday/.env` or `config.yaml` / launchd plist:

```bash
GROWW_GATEWAY_URL=http://76.13.241.82:8787
GROWW_GATEWAY_TOKEN=your-gateway-token-here
```

**Remove** `GROWW_API_KEY` and `GROWW_TOTP_SECRET` from your Mac — they should live **only on the VPS**.

Paper mode is unchanged (no gateway, no Groww).

### Smoke test from Mac

```bash
cd autoIntraday
export GROWW_GATEWAY_URL=http://76.13.241.82:8787
export GROWW_GATEWAY_TOKEN=your-token
python3 scripts/smoke_test_groww_auth.py
```

---

## 3. Security notes

- Always use a long random `GROWW_GATEWAY_TOKEN`.
- Prefer HTTPS later (nginx reverse proxy + TLS on a subdomain).
- Never commit Groww API keys or gateway tokens to git.
- The gateway exposes **live trading** — treat the token like a password.

---

## API endpoints (internal)

| Method | Path | Maps to |
|--------|------|---------|
| GET | `/health` | Liveness |
| GET | `/v1/ready` | Auth check |
| GET | `/v1/holdings` | Holdings |
| GET | `/v1/positions` | Positions |
| GET | `/v1/orders/open` | Open orders |
| POST | `/v1/orders` | Place order |
| POST | `/v1/orders/{id}/cancel` | Cancel order |
| … | … | Full parity with `groww_client.py` |
