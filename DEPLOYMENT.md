# Deployment

The dashboard is designed to run as a single web service on Render with
Cloudflare in front for DNS, WAF, caching, and SSO. The bot loop runs
inside the same process, controlled from the dashboard's Control page.

```
  Browser ──HTTPS──▶ Cloudflare ──HTTPS──▶ Render Web Service
                       │                       │
                       ├─ DNS                  ├─ Flask + gunicorn (web/app.py)
                       ├─ WAF + rate limit     ├─ BotController thread (web/bot_controller.py)
                       ├─ Cache /static/*      └─ Persistent disk → /app/data
                       └─ Access (SSO)              ├─ state.json
                                                    ├─ dashboard_history.json
                                                    └─ .env (config edits)
```

---

## Part 1 — Deploy on Render

### Option A: Blueprint (recommended)

1. Push this repo to GitHub.
2. In Render: **New → Blueprint → connect this repo**.
3. Render reads `render.yaml` and provisions a web service named
   `gov-contract-watch` with a 1 GB persistent disk.
4. In the Render UI, fill in the env vars marked `sync: false`:
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
   - `DASHBOARD_TOKEN` (pick a long random string — see below)
   - `SLACK_WEBHOOK` (optional)
5. Click **Apply**. Render builds the Docker image and starts the service.
6. Once the build is green, open the `https://<service>.onrender.com` URL.
   You'll be prompted for the `DASHBOARD_TOKEN` you set.
7. Sign in → **Control → Start bot** (or set `BOT_AUTOSTART=true` so it
   starts with the service).

### Option B: Manual web service

1. **New → Web Service → Docker** in Render.
2. Set port to `8000`, healthcheck path to `/api/health`.
3. Add a **persistent disk** mounted at `/app/data` (1 GB is plenty).
4. Copy every env var from `.env.example` into the Render env editor;
   override `STATE_FILE`, `DASHBOARD_HISTORY_FILE`, and
   `DASHBOARD_DOTENV_PATH` to point under `/app/data/`.
5. Deploy.

### Generating a `DASHBOARD_TOKEN`

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Treat it like a password. The token gates every page and JSON endpoint
except `/api/health`, `/login`, and `/static/*`.

---

## Part 2 — Put Cloudflare in front

These steps assume you own a domain on Cloudflare (e.g. `example.com`)
and want the dashboard at `bot.example.com`.

### 1. DNS

In the Cloudflare dashboard for your zone:

1. **DNS → Records → Add record**
   - Type: `CNAME`
   - Name: `bot` (or whatever subdomain you want)
   - Target: `<service>.onrender.com`
   - Proxy status: **Proxied** (orange cloud)
2. In Render: **Settings → Custom Domain → Add `bot.example.com`**.
   Render verifies the CNAME and provisions a TLS cert automatically.

### 2. SSL / TLS

In Cloudflare → **SSL/TLS → Overview**:

- Set the encryption mode to **Full (strict)**. Render serves real certs
  so strict mode works and prevents the half-encrypted "Flexible" mode
  that some setups land in by default.

### 3. Cache rules

The dashboard data is dynamic, but `/static/*` is cacheable.

In Cloudflare → **Caching → Cache Rules → Create rule**:

| Field | Value |
|---|---|
| Name | `Cache dashboard static assets` |
| If incoming requests match | `(http.request.uri.path matches "^/static/")` |
| Then | **Cache eligibility: Eligible for cache** · **Edge TTL: 1 day** |

Add a second rule to **bypass** cache for everything else (so the
snapshot API and HTML pages are never stale):

| Field | Value |
|---|---|
| Name | `Bypass cache for dashboard app` |
| If | `(http.host eq "bot.example.com" and not http.request.uri.path matches "^/static/")` |
| Then | **Bypass cache** |

### 4. Cloudflare Access (SSO gate)

Replaces the `DASHBOARD_TOKEN` with real SSO. Leave the token in place as
defense-in-depth.

1. In Cloudflare Zero Trust → **Access → Applications → Add an application**
   → Self-hosted.
2. Application name: `Gov Contract Dashboard`. Domain: `bot.example.com`.
3. **Identity providers**: enable Google, GitHub, or one-time PIN.
4. **Policy → Add a policy**:
   - Action: **Allow**
   - Include: **Emails** → list the addresses allowed in.
5. Save. Visiting `bot.example.com` now requires SSO before the request
   ever reaches Render.

### 5. WAF + rate limiting

In Cloudflare → **Security → WAF → Rate limiting rules → Create**:

| Field | Value |
|---|---|
| Name | `Throttle bot lifecycle endpoints` |
| If incoming requests match | `(http.request.uri.path matches "^/api/(bot|positions|config)/")` |
| Then | **Block** when more than `30` requests per `1 minute` from same IP |

This prevents accidental flooding of `/api/bot/start` or
`/api/positions/<sym>/sell` from a runaway script.

### 6. Verify

```bash
# Should hit Cloudflare (returns CF response headers) and reach Render.
curl -I https://bot.example.com/api/health
# Expected: HTTP/2 200, server: cloudflare
```

If Access is enabled, hit it from a browser instead — `curl` won't pass
the SSO challenge unless you use a service token.

---

## Part 3 — Local development

```bash
cp .env.example .env
# Fill in ALPACA_API_KEY, ALPACA_SECRET_KEY
python web_app.py
# → http://localhost:8000
```

`web_app.py` uses Flask's dev server. For a closer-to-prod local run:

```bash
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 8 wsgi:app
```

To skip the auth gate locally, leave `DASHBOARD_TOKEN` unset.

---

## Operational notes

- **Single worker is intentional.** `BotController` is an in-process
  singleton owning a thread, log buffer, and counters. Running 2 gunicorn
  workers would spawn 2 bot loops and double-trade.
- **Persistent disk.** `state.json` (seen award IDs), `dashboard_history.json`
  (snapshot deltas), and the dashboard-edited `.env` all live under
  `/app/data` and survive restarts.
- **First boot.** The bot won't trade until you click **Start** on the
  Control page (or set `BOT_AUTOSTART=true`). Use **Run one cycle now**
  to validate config before going live.
- **Going live.** `ALPACA_PAPER=true` is the default. Flip to `false` only
  after several paper-trading cycles look right.
