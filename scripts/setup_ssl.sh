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
