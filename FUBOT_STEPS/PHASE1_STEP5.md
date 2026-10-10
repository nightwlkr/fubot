# Phase 1 — Step 5: Nginx + SSL Setup
## FUBOT: Web Server, SSL & WebSocket Proxy

---

## Objective
Configure Nginx as a reverse proxy for FUBOT on `fubot.bedikian.cc` with:
- HTTPS (Let's Encrypt / certbot)
- WebSocket (WSS) upgrade support
- Proper security headers
- HTTP → HTTPS redirect

---

## Context
- **Domain**: `fubot.bedikian.cc`
- **VPS**: 10.10.30.25 (Debian 13)
- **Backend runs on**: port 8000 (uvicorn)
- **Frontend runs on**: port 3000 (Vite dev) or built static files served by Nginx
- **Depends on**: Phase 1 Step 4 (backend app starts)

---

## Nginx Configuration File: `nginx/fubot.conf`

Create at `/etc/nginx/sites-available/fubot.conf` (symlink to `/etc/nginx/sites-enabled/fubot.conf`):

```nginx
# Rate limiting
limit_req_zone $binary_remote_addr zone=fubot_api:10m rate=10r/s;

server {
    listen 80;
    server_name fubot.bedikian.cc;

    # Certbot challenge
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    # Redirect HTTP → HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name fubot.bedikian.cc;

    # SSL Certificate (certbot)
    ssl_certificate /etc/letsencrypt/live/fubot.bedikian.cc/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/fubot.bedikian.cc/privkey.pem;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:50m;
    ssl_session_tickets off;

    # Modern TLS
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' wss://fubot.bedikian.cc https://fubot.bedikian.cc; img-src 'self' data:; font-src 'self';" always;

    # Gzip
    gzip on;
    gzip_types text/plain application/json application/javascript text/css;

    # Static frontend files (built by frontend agent)
    root /home/vick/fubot/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # API proxy to FastAPI backend
    location /api/ {
        limit_req zone=fubot_api burst=20 nodelay;

        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # WebSocket proxy
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # WebSocket timeouts (longer)
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;

        # Disable buffering for WebSocket
        proxy_buffering off;
    }

    # Health check (no auth, no proxy — for load balancer probes)
    location /health {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    # Logs
    access_log /var/log/nginx/fubot_access.log;
    error_log /var/log/nginx/fubot_error.log;
}
```

---

## Deliverables

### 1. `nginx/fubot.conf`
File as shown above. Note: CSP header must allow WebSocket connections to `wss://fubot.bedikian.cc`.

### 2. `scripts/setup_ssl.sh`
Script to:
```bash
#!/bin/bash
set -e

# 1. Install certbot
apt-get update && apt-get install -y certbot python3-certbot-nginx

# 2. Stop Nginx temporarily
systemctl stop nginx

# 3. Generate SSL certificate
certbot certonly --standalone \
    --preferred-challenges http-01 \
    --domains fubot.bedikian.cc \
    --email admin@bedikian.cc \
    --agree-tos \
    --non-interactive \
    --keep-until-expiring

# 4. Create required directories
mkdir -p /var/www/certbot/.well-known/acme-challenge
mkdir -p /etc/nginx/sites-available
mkdir -p /etc/nginx/sites-enabled

# 5. Install Nginx config
cp /home/vick/fubot/nginx/fubot.conf /etc/nginx/sites-available/fubot.conf
ln -sf /etc/nginx/sites-available/fubot.conf /etc/nginx/sites-enabled/fubot.conf

# 6. Test Nginx config
nginx -t

# 7. Start Nginx
systemctl start nginx

# 8. Auto-renewal cron job
echo "0 0 * * * certbot renew --quiet" | tee /etc/cron.d/certbot-fubot
```

### 3. Install & Configure
Run as root on the VPS:
```bash
# As root on 10.10.30.25:
bash /home/vick/fubot/scripts/setup_ssl.sh
```

### 4. Validation
```bash
# 1. Check Nginx config
nginx -t

# 2. Check SSL certificate exists
ls /etc/letsencrypt/live/fubot.bedikian.cc/

# 3. Test HTTPS
curl -k https://fubot.bedikian.cc/api/health  # Should return {"status": "ok"}

# 4. Test HTTP → HTTPS redirect
curl -I http://fubot.bedikian.cc/  # Should 301 to https://

# 5. Test WebSocket proxy
# Connect wscat or websocat to wss://fubot.bedikian.cc/ws
```

---

## Dependencies
- **Requires**: Phase 1 Step 4 (backend app running on port 8000)

## Files Modified/Created
- `/etc/nginx/sites-available/fubot.conf` (on VPS)
- `/etc/nginx/sites-enabled/fubot.conf` (symlink on VPS)
- `/home/vick/fubot/nginx/fubot.conf` (committed to repo)
- `/home/vick/fubot/scripts/setup_ssl.sh`

## Next Step
Phase 1 Step 6: systemd Service Setup

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
