"""Bot control schemas."""
from typing import Optional
from pydantic import BaseModel


class BotStatusResponse(BaseModel):
    status: str  # "running" or "stopped"
    mode: str  # "paper" or "live"
    uptime_seconds: Optional[float] = 0
    scan_count: int = 0
    last_scan: Optional[str] = None
    daily_stats: Optional[dict] = None
    open_positions: Optional[list[dict]] = None
    enabled_symbols: Optional[list[str]] = None


class BotStartRequest(BaseModel):
    mode: str | None = None  # "paper" or "live"; None = use saved mode
