# Phase 1 — Step 4: User Auth — Models, Service & Login Endpoint
## FUBOT: JWT Authentication System

---

## Objective
Implement the complete authentication system: user model, password hashing with bcrypt, JWT token issuance, and the login/logout API endpoints. This is the security foundation for the entire dashboard.

---

## Context
- **Auth method**: JWT stored in httpOnly secure cookie
- **Password hashing**: bcrypt, minimum 12 rounds
- **Rate limiting**: 5 failed attempts per 15 minutes per IP
- **Depends on**: Phase 1 Steps 1 and 2 (models, database setup)

---

## Part A: User Model & Schemas

### SQLAlchemy Model (`backend/models/user.py`)
```python
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

### Pydantic Schemas (`backend/schemas/auth.py`)
```python
class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

class LoginResponse(BaseModel):
    token: str
    user: UserResponse

class UserResponse(BaseModel):
    id: int
    username: str
    role: str

class TokenPayload(BaseModel):
    sub: int  # user id
    username: str
    role: str
    exp: datetime
```

---

## Part B: Auth Service (`backend/services/auth_service.py`)

```python
class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def authenticate(self, username: str, password: str, ip: str) -> User | None:
        """
        Verify username + password.
        Returns User if valid, None if invalid.
        Tracks failed attempts for rate limiting.
        """
        # 1. Check if IP is rate-limited (5 failed attempts in 15 min)
        # 2. Fetch user by username
        # 3. Verify password with bcrypt.checkpw()
        # 4. On success: reset failed attempts counter
        # 5. On failure: increment failed attempts counter
        # 6. Never reveal whether username or password was wrong (same error message)

    def create_access_token(self, user: User) -> str:
        """
        Creates a JWT token with:
        - sub: user id
        - username
        - role
        - exp: 24 hours from now
        Uses HS256 algorithm with FUBOT_SECRET_KEY.
        """

    async def get_current_user(self, token: str) -> User | None:
        """
        Decodes JWT, returns User from DB.
        Returns None if token invalid or expired.
        """
```

### Rate Limiting Implementation
- Store failed attempts in a **memory dictionary** (not DB — no persistence needed):
  ```python
  failed_attempts: dict[str, list[datetime]] = {}
  ```
- Key = IP address
- Value = list of timestamps of failed logins in last 15 minutes
- On 6th failure within 15 minutes → reject with `429 Too Many Requests`
- On successful login → clear the IP's failed attempts

---

## Part C: Auth Router (`backend/routers/auth.py`)

### Endpoints

#### `POST /api/auth/login`
```python
@router.post("/login")
async def login(request: Request, credentials: LoginRequest):
    """
    Body: { "username": "...", "password": "..." }
    Success: 200 + { "token": "eyJ...", "user": { "id": 1, "username": "admin", "role": "admin" } }
    Failure (bad creds): 401 + { "detail": "Invalid credentials" }
    Failure (rate limited): 429 + { "detail": "Too many attempts, try again in X minutes" }
    Sets httpOnly, Secure, SameSite=Lax cookie: "fubot_token=..."
    """
```

#### `POST /api/auth/logout`
```python
@router.post("/logout")
async def logout():
    """
    Clears the fubot_token cookie.
    Always returns 200: { "ok": true }
    """
```

#### `GET /api/auth/me`
```python
@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user_from_cookie)):
    """
    Returns current authenticated user.
    No token / invalid token: 401 + { "detail": "Not authenticated" }
    Success: 200 + { "id": 1, "username": "admin", "role": "admin" }
    """
```

### Dependency: `get_current_user_from_cookie`
```python
async def get_current_user_from_cookie(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Extracts fubot_token from cookie.
    Decodes and validates JWT.
    Returns User or raises 401.
    """
```

---

## Part D: Update `backend/main.py`

Add to the FastAPI app:
```python
from backend.routers import auth, dashboard, positions, trades, signals, settings, orders, bot, performance

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
# Add all other routers (they'll be implemented in later steps — import stubs for now)
```

---

## Deliverables

### 1. `backend/models/user.py`
SQLAlchemy model for `users` table.

### 2. `backend/schemas/auth.py`
All Pydantic request/response models.

### 3. `backend/services/auth_service.py`
- `AuthService` class
- JWT creation/validation
- bcrypt password hashing
- Rate limiting (in-memory)

### 4. `backend/routers/auth.py`
All three endpoints with proper HTTP status codes.

### 5. Update `backend/database.py`
Add `get_db()` dependency that yields `AsyncSession`.

### 6. Update `backend/main.py`
Include auth router and all future routers (stubs).

### 7. Validation
- `POST /api/auth/login` with correct creds → 200 + JWT cookie set
- `POST /api/auth/login` with wrong creds → 401
- `POST /api/auth/login` 6 times wrong → 429
- `GET /api/auth/me` with valid cookie → 200 + user data
- `GET /api/auth/me` with no cookie → 401
- `POST /api/auth/logout` → 200 + cookie cleared

---

## Dependencies
- **Requires**: Phase 1 Steps 1 and 2

## Files Modified/Created
- `backend/models/user.py`
- `backend/schemas/auth.py`
- `backend/services/auth_service.py`
- `backend/routers/auth.py`
- `backend/database.py` (add `get_db`)
- `backend/main.py` (add routers)

## Next Step
Phase 1 Step 5: Nginx + SSL Setup

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
