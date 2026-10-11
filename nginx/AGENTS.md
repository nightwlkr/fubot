# nginx/ — Production Reverse Proxy & SSL

## Purpose

Nginx configuration for the production FUBOT deployment at https://fubot.bedikian.cc
(VPS `contabo01`). Handles SSL termination, reverse proxy to the built frontend and
the FastAPI backend (port 8000), rate limiting, security headers, and WebSocket upgrades.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/nginx/`
Status: **deployed** — site installed at `/etc/nginx/sites-available/fubot` (symlinked into
`sites-enabled`), Let's Encrypt cert issued (webroot), HSTS live. Changes deploy via
`git pull` on the VPS → `cp nginx/fubot.conf /etc/nginx/sites-available/fubot` → `nginx -t` → reload.

## Local Contracts

- Single site file `nginx/fubot.conf`; on the VPS it is linked into the nginx include path
- Port 80: ACME challenge root `/var/www/certbot`, everything else 301 → HTTPS
- Port 443 (TLSv1.2/1.3): serves static frontend from `/opt/fubot/frontend/dist` with SPA
  fallback (`try_files $uri $uri/ /index.html`); `/opt/fubot/frontend/dist` must stay
  world-readable (nginx worker runs as www-data)
- API: `location /api/` → `http://127.0.0.1:8000` with `limit_req` 10 r/s burst 20 AND
  WebSocket upgrade headers (`Upgrade`/`Connection $connection_upgrade` via the http-level
  `map`) — the production frontend dials `/api/ws`
- WebSocket: `location /ws` → backend with upgrade headers and 7-day timeouts
- Health: `location /health` → backend `/health`
- Domain: `fubot.bedikian.cc`; certs at `/etc/letsencrypt/live/fubot.bedikian.cc/`
  (renewal: certbot's webroot challenge keeps working through the port-80 block)

## Work Guidance

### Security Headers (as configured)
- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy` — `default-src 'self'`, script/style `unsafe-inline`,
  `connect-src wss://fubot.bedikian.cc https://fubot.bedikian.cc`

### Verified (2026-10-11)
- `nginx -t` clean; HTTPS 200 for `/` and `/health`; SPA loads; HSTS + headers present
- `wss://fubot.bedikian.cc/ws` and `/api/ws` both complete the auth handshake

### Commands
```bash
ssh contabo01
sudo nginx -t && sudo systemctl reload nginx   # after any conf change
sudo certbot certificates                       # SSL expiry
```

## Verification

- `nginx -t` returns `syntax is ok`
- `curl -I https://fubot.bedikian.cc` → 200 with security headers incl. HSTS
- API responds at `https://fubot.bedikian.cc/health`
- `wss://fubot.bedikian.cc/api/ws` completes the auth handshake

## Child DOX Index

No child AGENTS.md — a single config file, self-documenting.
