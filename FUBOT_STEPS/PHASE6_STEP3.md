# Phase 6 — Step 3: Security Audit
## FUBOT: Verify All Security Controls

---

## Objective
Audit all security controls: API keys encrypted, no secrets in logs, JWT httpOnly cookie, SSL verified, rate limiting active, and no code injection vectors.

---

## Context
- **Depends on**: All phases complete
- **Audience**: This audit should be done by the DevOps agent and reviewed by the user before going live

---

## Security Checklist

### 1. API Key Encryption

Verify the API key is encrypted in the database:

```bash
# On VPS, check the encrypted value in DB
cd /home/vick/fubot/backend
python3 -c "
import asyncio
from database import async_session
from models.account import Account

async def check_key():
    async with async_session() as db:
        result = await db.execute('SELECT api_key FROM account LIMIT 1')
        row = result.fetchone()
        if row:
            key = row[0]
            print(f'API key in DB (first 20 chars): {key[:20]}...')
            # If it starts with 'enc:' it's encrypted
            print(f'Is encrypted: {key.startswith(\"enc:\")}')

asyncio.run(check_key())
"
```

Expected: The stored key should start with `enc:aes_gcm:` or similar (indicating it's encrypted, not plaintext). Plaintext keys are a **FAIL**.

---

### 2. No Secrets in Logs

```bash
# Check backend logs for any leaked secrets
sudo journalctl -u fubot --since "1 hour ago" | grep -i "api.key\|api_secret\|password\|token" || echo "No secrets found in logs"
```

Expected: No output (no secrets logged) = **PASS**.

If secrets appear, search for the leak:
```bash
# Find the leak
grep -rn "api_key\|api_secret" /home/vick/fubot/backend/services/ --include="*.py"
# Ensure they're only in encrypted/decrypted contexts, never in print() or logger
```

---

### 3. JWT Configuration

```bash
# Check that JWT is configured with httpOnly + secure flags
grep -n "httpOnly\|secure\|same_site" /home/vick/fubot/backend/core/security.py
```

Expected: JWT cookie should have `httpOnly=True`, `secure=True`, `samesite="lax"`.

Also verify in the response headers:
```bash
curl -I -X POST https://fubot.bedikian.cc/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"wrong"}'
```

Expected: Set-Cookie header contains `HttpOnly`, `Secure`, `SameSite`.

---

### 4. SSL Certificate Valid

```bash
# Check SSL certificate validity
openssl s_client -connect fubot.bedikian.cc:443 -servername fubot.bedikian.cc </dev/null 2>/dev/null | \
  openssl x509 -noout -dates -issuer

# Also check with curl
curl -I https://fubot.bedikian.cc/api/auth/me
```

Expected: Certificate is valid, not expired. HTTPS redirect works.

---

### 5. Rate Limiting

```bash
# Send 10 rapid login requests (should be rate limited after 5)
for i in {1..10}; do
  echo "Request $i:"
  curl -s -o /dev/null -w "%{http_code}" \
    -X POST https://fubot.bedikian.cc/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"wrongpassword"}'
  echo ""
done
```

Expected: First ~5 return 401 (wrong password), then 429 (rate limited).

---

### 6. No SQL Injection

Test all API endpoints with injection payloads:

```bash
TOKEN="your_token"

# SQL injection in symbol filter
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://fubot.bedikian.cc/api/trades?symbol=BTCUSDT'; DROP TABLE trades;--" | \
  python3 -c "import sys, json; d=json.load(sys.stdin); print('SQL injection blocked' if 'error' in str(d) else 'POSSIBLE INJECTION')"
```

Expected: Either returns normal response (parameterized query protected) or error, but **never** executes the DROP TABLE.

---

### 7. No Path Traversal

```bash
# Try path traversal on settings endpoint
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://fubot.bedikian.cc/api/settings/../../../etc/passwd"
```

Expected: 404 or 400, **NOT** file contents.

---

### 8. API Key Binance Permissions Check

```bash
# Log into Binance.com and verify the API key:
# 1. Go to API Management
# 2. Confirm the key has ONLY:
#    - Enable Futures ✓
#    - Trade (Futures) ✓
#    - Order (Futures) ✓
#    - Position (Futures) ✓
#    - Enable Spot & Margin Trading ✗ (should be OFF)
#    - Enable Withdrawal ✗ (should be OFF)
#    - Enable Internal Transfer ✗ (should be OFF)
```

Take screenshots and share with the user for manual verification.

---

### 9. Bot Process Runs as Non-Root

```bash
# Check what user the fubot process runs as
ps aux | grep -E "uvicorn|python.*main" | grep -v grep
```

Expected: Process runs as `vick` or `fubot` user, NOT `root`.

```bash
# Also check the systemd service
cat /etc/systemd/system/fubot.service | grep "User=\|Group="
```

Expected: `User=vick` (or a dedicated `fubot` user), NOT `root`.

---

### 10. Firewall Check

```bash
# Verify only required ports are open
sudo ufw status
```

Expected:
```
To                         Action      From
--                         ------      ----
22/tcp                     LIMIT       Anywhere
80/tcp                     ALLOW       Anywhere
443/tcp                    ALLOW       Anywhere
```

Expected: No other ports (3306, 5432) exposed publicly.

---

## Security Audit Report

Document findings:

| Check | Status | Notes |
|---|---|---|
| API key encrypted in DB | PASS/FAIL | |
| No secrets in logs | PASS/FAIL | |
| JWT httpOnly + secure | PASS/FAIL | |
| SSL certificate valid | PASS/FAIL | |
| Rate limiting active | PASS/FAIL | |
| SQL injection blocked | PASS/FAIL | |
| Path traversal blocked | PASS/FAIL | |
| API key permissions correct | PASS/FAIL | |
| Bot runs as non-root | PASS/FAIL | |
| Firewall configured | PASS/FAIL | |

Any **FAIL** must be fixed before the bot goes live.

---

## Deliverables

1. Completed security checklist with PASS/FAIL for each item
2. Any FAIL items fixed
3. Screenshots of Binance API key permissions (user confirms manually)
4. Signed-off by user before live trading

---

## Next Step
Phase 6 Step 4: Load Test WebSocket

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
