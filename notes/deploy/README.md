# Deploying Simply Notes to a Hostinger VPS

Target: **https://notes.noobius.in**

Simply Notes has no database, no accounts and no server-side state — everything
a user writes lives in their own browser. That makes deployment unusually
boring: one container, one reverse proxy, no backups to arrange, nothing to
migrate. If the container is destroyed and rebuilt, no user loses anything.

---

## What you need before starting

| Thing | Where |
|---|---|
| VPS IP address | Hostinger → VPS → Overview |
| SSH access as root | Hostinger → VPS → SSH access |
| DNS control for `noobius.in` | wherever the domain's nameservers point |
| An email address | for the Let's Encrypt expiry notices |

---

## Step 1 — Point the domain at the VPS

Add an **A record** in whatever manages DNS for `noobius.in`. If the
nameservers are Hostinger's, that is hPanel → Domains → DNS / Nameservers.

| Type | Name | Points to | TTL |
|---|---|---|---|
| `A` | `notes` | *your VPS IPv4* | 300 |

Add an `AAAA` record for `notes` pointing at the VPS IPv6 as well, if it has
one.

Do this **first**. Certbot proves you control the domain by answering an HTTP
request on it, so the record has to resolve before Step 3 can finish.

Check it from your laptop:

```bash
dig +short notes.noobius.in
# should print your VPS IP
```

Give it a few minutes if it does not. A TTL of 300 keeps that wait short.

---

## Step 2 — Prepare the server (once)

```bash
scp deploy/bootstrap-vps.sh root@<VPS_IP>:/tmp/
ssh root@<VPS_IP> "bash /tmp/bootstrap-vps.sh notes.noobius.in you@example.com"
```

This installs Docker, nginx and certbot, opens ports 80 and 443 in the
firewall, writes the reverse-proxy config, and obtains the TLS certificate with
automatic renewal.

It deliberately does **not** deploy the app — the proxy will return 502 until
Step 3, which is expected.

---

## Step 3 — Deploy

From this directory on your laptop:

```bash
./deploy/deploy.sh root@<VPS_IP>
```

It runs lint, types and tests locally first, then copies the source, builds the
image on the server and restarts the container. `node_modules` and `.next` are
never copied — they are rebuilt on the server, so no macOS-native binary ever
reaches a Linux host.

The container listens on **127.0.0.1:8085**, not on a public interface. Only
nginx can reach it.

To deploy with analytics enabled:

```bash
NEXT_PUBLIC_GA_MEASUREMENT_ID=G-XXXXXXXXXX ./deploy/deploy.sh root@<VPS_IP>
```

`NEXT_PUBLIC_*` values are compiled into the client bundle, so they are build
arguments rather than runtime environment. Changing the measurement ID means
redeploying.

---

## Step 4 — Check it

```bash
curl -I https://notes.noobius.in
curl -s https://notes.noobius.in/robots.txt
```

Then open it in a browser and confirm:

- it lands on the **Notes** tab, with `?tab=notes` in the address bar
- a task added in **Todos** survives a refresh
- the padlock is present and the certificate names `notes.noobius.in`
- **Install app** is offered (the PWA manifest and service worker are being served)

---

## Redeploying

Run Step 3 again. It rebuilds and restarts in place; there is nothing to
migrate and no downtime worth planning for beyond a couple of seconds.

---

## Operating it

```bash
ssh root@<VPS_IP>

docker ps                                   # is it up
docker logs -f simply-notes-web             # what is it saying
docker inspect --format '{{.State.Health.Status}}' simply-notes-web
docker restart simply-notes-web

nginx -t && systemctl reload nginx           # after editing proxy config
certbot renew --dry-run                      # confirm renewal still works
```

### If the site returns 502

nginx is up but the container is not answering.

```bash
docker ps -a | grep simply-notes
docker logs --tail 50 simply-notes-web
curl -I http://127.0.0.1:8085
```

### If certbot fails

Almost always DNS. Confirm `dig +short notes.noobius.in` returns the VPS IP
from a machine outside the VPS, then re-run:

```bash
certbot --nginx -d notes.noobius.in
```

---

## A note on user data

There is no server-side storage to back up. Every task, activity, note and
folder lives in the visitor's own browser (IndexedDB), which also means:

- wiping the server loses nothing of theirs
- **they** are responsible for their own backups, via Settings → Export JSON
- the same person on two devices has two independent sets of notes

That is the trade the local-first design makes, and it is stated plainly on the
site's own privacy page.
