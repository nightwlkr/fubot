# nginx/ — Production Reverse Proxy & SSL

## Purpose

Nginx configuration for the production FUBOT deployment. Handles SSL termination, reverse proxy to the built frontend and the FastAPI backend (port 8000), rate limiting, and security headers.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/nginx/`
Status: `fubot.conf` written — not deployed to any server; certbot has not run; no certs in repo

## Local Contracts

- Single site file `nginx/fubot.conf` — no `nginx.conf` or `sites-enabled/` in the repo; on the VPS it gets linked into the nginx include path
- Port 80: ACME challenge root `/var/www/certbot`, everything else 301 → HTTPS
- Port 443 (TLSv1.2/1.3): serves static frontend from `/home/vick/fubot/frontend/dist` with SPA fallback (`try_files $uri $uri/ /index.html`)
- API: `location /api/` → `http://127.0.0.1:8000`, rate limit 10 r/s burst 20 (`fubot_api` zone)
- WebSocket: `location /ws` → `http://127.0.0.1:8000` with upgrade headers and 7-day timeouts
- Health: `location /health` → backend `/health`
- Domain: `fubot.bedikian.cc`; certs expected at `/etc/letsencrypt/live/fubot.bedikian.cc/`

## Work Guidance

### Security Headers (as configured)
- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy` — `default-src 'self'`, script/style `unsafe-inline`, `connect-src wss://fubot.bedikian.cc https://fubot.bedikian.cc`
- No HSTS header configured — add `Strict-Transport-Security` before the PHASE6 security audit sign-off

### Known Mismatch
- The frontend dials `/api/ws`; only `location /ws` and the generic `location /api/` exist, so `/api/ws` is proxied as a plain API request. Align the WS path when the backend route is mounted.

### Commands
```bash
sudo nginx -t                      # test config before reload
sudo systemctl reload nginx        # apply config change
sudo certbot certificates          # check SSL cert expiry
```

## Verification

- `nginx -t` returns `syntax is ok` and `test is successful`
- `curl -I https://fubot.bedikian.cc` returns 200 with a valid certificate (after deploy)
- Frontend loads at `https://fubot.bedikian.cc`
- API responds at `https://fubot.bedikian.cc/health`

## Child DOX Index

No child AGENTS.md — a single config file, self-documenting.
