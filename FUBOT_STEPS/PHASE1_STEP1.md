# Phase 1 — Step 1: Project Foundation
## FUBOT: Project Scaffold, Config & Dependencies

---

## Objective
Set up the complete project directory structure for FUBOT, install all Python dependencies, create the `config.py` with pydantic validation, and produce a working `.env.example`.

---

## Context
- **VPS**: 10.10.30.25 (Debian 13, same as BBOT)
- **Project root**: `/home/vick/fubot/`
- **Python**: 3.11+, async-first
- **Database**: PostgreSQL (same VPS, port 5432)

---

## Directory Structure to Create

```
/home/vick/fubot/
├── backend/
│   ├── main.py                    # FastAPI app entry point — minimal for now
│   ├── config.py                  # Environment config + pydantic validation
│   ├── database.py                # Async SQLAlchemy setup
│   ├── models/                    # ORM models (empty __init__ for now)
│   ├── schemas/                   # Pydantic models (empty __init__ for now)
│   ├── routers/                   # API routes (empty __init__ for now)
│   ├── services/                  # Business logic (empty __init__ for now)
│   ├── core/                      # Bot engine (empty __init__ for now)
│   ├── indicators/                # TA modules (empty __init__ for now)
│   └── utils/                     # Utilities (empty __init__ for now)
├── frontend/                      # Scaffolded separately in Phase 5
├── nginx/
├── scripts/
├── alembic/
├── tests/
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
├── pyproject.toml
├── requirements.txt
├── .env.example
├── fubot.service
└── README.md
```

---

## Deliverables

### 1. `requirements.txt`
All Python dependencies:
```
fastapi==latest
uvicorn[standard]==latest
sqlalchemy[asyncio]==latest
asyncpg==latest
alembic==latest
python-binance==latest
pandas==latest
pandas-ta==latest
pydantic==latest
pydantic-settings==latest
python-jose[cryptography]==latest
passlib[bcrypt]==latest
cryptography==latest
python-multipart==latest
websockets==latest
httpx==latest
pytest==latest
pytest-asyncio==latest
```

### 2. `pyproject.toml`
Standard Python project config with project metadata and tool configs (ruff, mypy, pytest).

### 3. `backend/config.py`
Pydantic-based settings loaded from environment variables. **No hardcoded values.**

```python
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    # Core
    FUBOT_ENV: str = Field(default="development")
    FUBOT_SECRET_KEY: str = Field(min_length=32)
    FUBOT_ENCRYPTION_KEY: str = Field(min_length=32)

    # Database
    DATABASE_URL: str

    # Binance
    BINANCE_API_KEY: str
    BINANCE_API_SECRET: str
    BINANCE_TESTNET: bool = True

    # Admin
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str

    # VPS
    VPS_HOST: str
    DOMAIN: str

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
```

Validate the config loads correctly. If any required env var is missing, the app should crash on startup with a clear error message listing the missing variable.

### 4. `.env.example`
Every environment variable documented with a comment explaining its purpose and an example value:
```bash
# FUBOT Core
FUBOT_ENV=development
FUBOT_SECRET_KEY=<your-32-char-secret-key>
FUBOT_ENCRYPTION_KEY=<your-32-char-encryption-key>

# Database
DATABASE_URL=postgresql+asyncpg://fubot:<password>@localhost:5432/fubot

# Binance
BINANCE_API_KEY=your-binance-api-key-here
BINANCE_API_SECRET=your-binance-secret-here
BINANCE_TESTNET=true

# Admin User
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<your-admin-password>

# VPS / Network
VPS_HOST=10.10.30.25
DOMAIN=fubot.bedikian.cc
```

### 5. `backend/database.py`
Async SQLAlchemy setup using `create_async_engine` and `AsyncSession`. Should support `alembic` and direct usage.

### 6. `backend/main.py`
Minimal FastAPI app:
- Create FastAPI instance
- Include CORS middleware (allow frontend origin)
- Health check route: `GET /health` → `{"status": "ok"}`
- No auth on health check
- No routers included yet (added in later steps)

### 7. Validate
- Run `pip install -r requirements.txt` — no errors
- Run `python -c "from backend.config import settings; print(settings.DATABASE_URL)"` — should load without error (env vars can be mock for validation)
- Run `uvicorn backend.main:app --host 0.0.0.0 --port 8000` — server starts without error
- `curl http://localhost:8000/health` returns `{"status": "ok"}`

---

## Dependencies
- None — this is the first step

## Files Modified/Created
All files under `/home/vick/fubot/` as listed above

## Next Step
Phase 1 Step 2: PostgreSQL Tables with Alembic Migrations

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
