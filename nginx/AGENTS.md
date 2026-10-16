# nginx/ — Legacy Reverse-Proxy config (NPM cutover 2026-10-16)

## Status

**No longer used in production.** Nginx Proxy Manager (jc21/nginx-proxy-manager)
on the same VPS now owns 80/443, and the Fubot proxy host forwards to
`http://127.0.0.1:8000` (the FastAPI backend also serves the SPA).

This folder is kept as a historical record + a working reference for the
letencrypt + HSTS + WebSocket upgrade patterns if you ever need to
recreate the site config outside NPM.

## Legacy commands (no longer needed for deploys)

```bash
ssh contabo01
sudo nginx -t && sudo systemctl reload nginx   # after any conf change
sudo certbot certificates                       # SSL expiry
```

The current deploy path for proxy changes is the NPM UI
(https://contabo01:81 or the public port) — or the NPM API:
`PUT /api/nginx/proxy-hosts/{id}` (auth: `POST /api/tokens`).
Reload: `docker exec nginx-app-1 /usr/sbin/nginx -s reload`.
