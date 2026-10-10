# Phase 1 — Step 6: systemd Service Setup
## FUBOT: Keep the Bot Running Forever

---

## Objective
Create a systemd service unit file so FUBOT starts automatically on boot, restarts on crash, and can be managed with standard commands. Also create a non-root system user to run the bot process.

---

## Context
- **VPS**: 10.10.30.25 (Debian 13)
- **Python process**: runs on port 8000
- **Process manager**: systemd
- **Non-root user**: `fubot` (create this)
- **Depends on**: Phase 1 Step 4 (backend app starts)

---

## Part A: System User

Create a non-root user to run the FUBOT process:

```bash
# On VPS, as root:
useradd -r -s /bin/false -d /home/fubot -m fubot
chown fubot:fubot /home/vick/fubot -R  # Give fubot user access to the app dir
```

---

## Part B: systemd Service File

Create at `/etc/systemd/system/fubot.service`:

```ini
[Unit]
Description=FUBOT - Automated Binance Futures Trading Bot
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=fubot
Group=fubot
WorkingDirectory=/home/vick/fubot

# Environment
EnvironmentFile=/home/vick/fubot/.env

# Start command
ExecStart=/home/vick/fubot/venv/bin/python -m uvicorn backend.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 1 \
    --loop uvloop

# Restart policy
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=fubot

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/home/vick/fubot /var/log/fubot
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

---

## Part C: Log Directory

```bash
mkdir -p /var/log/fubot
chown fubot:fubot /var/log/fubot
```

---

## Part D: Service Management Commands

Create a helper script at `/home/vick/fubot/scripts/fubotctl.sh`:

```bash
#!/bin/bash
set -e

case "$1" in
  start)
    systemctl daemon-reload
    systemctl enable fubot
    systemctl start fubot
    echo "FUBOT started"
    ;;
  stop)
    systemctl stop fubot
    echo "FUBOT stopped"
    ;;
  restart)
    systemctl restart fubot
    echo "FUBOT restarted"
    ;;
  status)
    systemctl status fubot --no-pager
    ;;
  logs)
    journalctl -u fubot -f --since "1 hour ago"
    ;;
  *)
    echo "Usage: fubotctl {start|stop|restart|status|logs}"
    exit 1
    ;;
esac
```

---

## Deliverables

### 1. `fubot.service` (committed to repo)
File as shown above, saved at `/home/vick/fubot/fubot.service`.

### 2. `scripts/fubotctl.sh`
As shown above.

### 3. Install & Configure (on VPS as root)
```bash
# 1. Create fubot user
useradd -r -s /bin/false -d /home/fubot -m fubot 2>/dev/null || true

# 2. Set ownership
chown -R fubot:fubot /home/vick/fubot

# 3. Create log dir
mkdir -p /var/log/fubot
chown fubot:fubot /var/log/fubot

# 4. Make ctl script executable
chmod +x /home/vick/fubot/scripts/fubotctl.sh

# 5. Install service
ln -sf /home/vick/fubot/fubot.service /etc/systemd/system/fubot.service

# 6. Reload systemd
systemctl daemon-reload

# 7. Start
systemctl start fubot

# 8. Enable on boot
systemctl enable fubot
```

### 4. Validation
```bash
# Check service is running
systemctl status fubot

# Check it's enabled for boot
systemctl is-enabled fubot

# Check logs
journalctl -u fubot --since "5 minutes ago" --no-pager

# Simulate crash and verify auto-restart
kill $(pgrep -f "uvicorn backend.main")
sleep 15
systemctl status fubot  # Should be "active (running)"
```

---

## Dependencies
- **Requires**: Phase 1 Step 4 (backend app can start)

## Files Modified/Created
- `/etc/systemd/system/fubot.service` (on VPS)
- `/home/vick/fubot/fubot.service` (committed to repo)
- `/home/vick/fubot/scripts/fubotctl.sh`
- `/var/log/fubot/` (on VPS)

## Next Step
Phase 1 Step 7: Create Admin User

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
