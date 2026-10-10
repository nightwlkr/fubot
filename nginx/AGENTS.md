# nginx/ — Production Reverse Proxy & SSL

## Purpose

Nginx configuration for the production FUBOT deployment. Handles SSL termination, reverse proxy to frontend (port 80) and backend (port 8000), and basic security headers.

## Ownership

Owner: FUBOT project team
Location: `/Users/vick/Documents/Projects/fubot/nginx/`
Status: **Not yet scaffolded** — PHASE6 of the build steps

## Local Contracts

- SSL certificates via Certbot (Let's Encrypt) — auto-renewal configured
- Nginx runs as reverse proxy (not as a web server for static files in production)
- Frontend is served by Nginx on port 80
- Backend API proxied to `http://127.0.0.1:8000` at `/api/v1`
- WebSocket proxied at `/api/v1/ws`
- Domain: `fubot.bedikian.cc`

## Work Guidance

### Config Structure
```
nginx/
  nginx.conf        # Main config (include sites-enabled/)
  sites-enabled/
    fubot           # Main site config (SSL, proxy_pass)
  ssl/              # Let's Encrypt cert paths (managed by Certbot)
```

### Security Headers
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Strict-Transport-Security: max-age=31536000`
- `Content-Security-Policy` (restrictive, backend-only API)

### Commands
```bash
# Test config before reload
sudo nginx -t

# Reload after config change
sudo systemctl reload nginx

# Check SSL certificate expiry
sudo certbot certificates
```

## Verification

- `nginx -t` returns `syntax is ok` and `test is successful`
- `curl -I https://fubot.bedikian.cc` returns 200 with SSL certificate valid
- Frontend loads at `https://fubot.bedikian.cc`
- API responds at `https://fubot.bedikian.cc/api/v1/health`

## Child DOX Index

No child AGENTS.md needed — config files are straightforward and self-documenting.
