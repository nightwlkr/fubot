# Phase 6 — Step 5: Production Deploy — Nginx SSL + DNS + certbot
## FUBOT: Live at https://fubot.bedikian.cc

---

## Objective
Configure the production VPS to serve FUBOT at `https://fubot.bedikian.cc` with HTTPS, proper SSL certificates, Nginx reverse proxy, and WebSocket support.

---

## Context
- **Depends on**: Backend and frontend built, tested
- **Domain**: `fubot.bedikian.cc` (DNS CNAME already pointing to VPS IP)
- **VPS**: 10.10.30.25
- **Ports**: 80 (HTTP), 443 (HTTPS) exposed via firewall

---

## Step 1: Verify DNS Propagation

```bash
# From local machine, check DNS resolves
nslookup fubot.bedikian.cc

# Expected: returns the VPS IP (10.10.30.25 or whatever public IP the VPS has)
```

If DNS doesn't resolve, create the CNAME record at your DNS provider:
```
fubot.bedikian.cc  CNAME  300  bedikian.cc
# OR
fubot.bedikian.cc  A      300  <VPS public IP>
```

---

## Step 2: Nginx Configuration

Create `/home/vick/fubot/nginx/fubot.conf`:

```nginx
# Upstream: Backend FastAPI
upstream fubot_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

# Upstream: Frontend (Vite dev or static)
# For production build, serve static files directly instead:
# upstream fubot_frontend {
#     server 127.0.0.1:5173;
# }

server {
    listen 80;
    server_name fubot.bedikian.cc;

    # Redirect all HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name fubot.bedikian.cc;

    # SSL Certificate (certbot will populate these)
    ssl_certificate /etc/letsencrypt/live/fubot.bedikian.cc/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/fubot.bedikian.cc/privkey.pem;

    # SSL Security Settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # Security Headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Gzip
    gzip on;
    gzip_types text/plain application/json application/javascript text/css image/svg+xml;
    gzip_min_length 1000;

    # Frontend static files (production build)
    root /home/vick/fubot/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # API Proxy
    location /api/ {
        proxy_pass http://fubot_backend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # CORS (for dev tools / API testing)
        # In production, restrict to known origins
        add_header Access-Control-Allow-Origin "https://fubot.bedikian.cc" always;
        add_header Access-Control-Allow-Credentials "true" always;
        add_header Access-Control-Allow-Methods "GET, POST, PUT, PATCH, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type" always;

        # CORS preflight
        if ($request_method = 'OPTIONS') {
            add_header Access-Control-Allow-Origin "https://fubot.bedikian.cc";
            add_header Access-Control-Allow-Credentials "true";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, PATCH, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type";
            add_header Content-Length 0;
            add_header Content-Type text/plain;
            return 204;
        }
    }

    # WebSocket Proxy
    location /ws {
        proxy_pass http://fubot_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Disable buffering for WebSocket
        proxy_buffering off;
        proxy_read_timeout 86400;
    }
}
```

---

## Step 3: Install and Configure Nginx

```bash
# SSH to VPS
ssh vick@10.10.30.25

# Install nginx if not present
sudo apt-get update && sudo apt-get install -y nginx

# Test nginx config
sudo nginx -t

# Enable the FUBOT site
sudo ln -sf /home/vick/fubot/nginx/fubot.conf /etc/nginx/sites-enabled/fubot.conf

# Remove default site
sudo rm -f /etc/nginx/sites-enabled/default

# Test again
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

---

## Step 4: Let's Encrypt SSL Certificate

```bash
# Install certbot
sudo apt-get install -y certbot python3-certbot-nginx

# Obtain SSL certificate
sudo certbot --nginx -d fubot.bedikian.cc --noninteractive --agree-tos \
  --email your_email@bedikian.cc --webroot -w /var/www/html

# Verify auto-renewal
sudo certbot renew --dry-run
```

If certbot fails because port 80 is blocked, check firewall:
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

---

## Step 5: Build and Serve Frontend

```bash
# On VPS
cd /home/vick/fubot/frontend

# Build for production
npm run build

# Verify dist directory created
ls -la dist/

# The nginx config serves from /home/vick/fubot/frontend/dist
```

---

## Step 6: Restart Backend and Verify

```bash
# Restart the fubot service
sudo systemctl restart fubot
sudo systemctl status fubot

# Test HTTPS
curl -I https://fubot.bedikian.cc/api/auth/me

# Expected: HTTP 401 (no token), SSL certificate valid

# Test full login flow
curl -c /tmp/cookies.txt -X POST \
  https://fubot.bedikian.cc/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}'

# Should return token and set cookie
```

---

## Step 7: Final DNS + SSL Verification

```bash
# Check SSL grade
curl -I https://fubot.bedikian.cc

# Check SSL Labs (optional, from local machine):
# Visit https://www.ssllabs.com/ssltest/analyze.html?d=fubot.bedikian.cc
```

Expected: Valid SSL, no insecure protocols or ciphers.

---

## Nginx Log Locations

```bash
# Access logs
sudo tail -f /var/log/nginx/fubot.bedikian.cc-access.log

# Error logs
sudo tail -f /var/log/nginx/fubot.bedikian.cc-error.log

# Bot logs
sudo journalctl -u fubot -f
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| 502 Bad Gateway | Backend not running — `sudo systemctl restart fubot` |
| 502 on /ws | WebSocket route not configured — check `proxy_pass` and `Upgrade` header |
| SSL certificate error | Run `sudo certbot --nginx -d fubot.bedikian.cc` |
| DNS not resolving | Check CNAME at DNS provider, wait for propagation (up to 48h) |
| Too many redirects | Check `add_header Access-Control-Allow-Origin` — shouldn't be set on redirect |
| Certbot port 80 blocked | `sudo ufw allow 80/tcp` |

---

## Deliverables

1. `https://fubot.bedikian.cc` serves the dashboard with valid SSL
2. `https://fubot.bedikian.cc/api/*` proxies to FastAPI backend
3. `wss://fubot.bedikian.cc/ws` WebSocket works
4. Let's Encrypt auto-renewal is configured (cron job or timer)
5. Nginx logs are accessible
6. `certbot renew --dry-run` succeeds

---

## Next Step
Phase 6 Step 6: README.md Documentation

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
