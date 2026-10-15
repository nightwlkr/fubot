"""Shared open-position pricing for the dashboard/positions routers.

Parity with Binance's own position display:
- LIVE mode: read the exchange's positionRisk per open symbol and use its
  entryPrice / positionAmt / markPrice / unRealizedProfit / leverage directly.
  Recomputing PnL from (DB entry × last price) drifted from Binance on every
  axis: DB entry kept the signal midpoint instead of the fill, DB qty wasn't
  LOT_SIZE-rounded (89.332934 vs 89.3), and last-trade price ≠ mark price.
- PAPER mode: use the MARK price (/fapi/v1/premiumIndex — Binance's app
  computes unrealized PnL on mark price by default), computed PnL.

ROI = unrealized PnL / initial margin (Binance's definition), where
initial margin = entry × qty / leverage.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _roi_pct(pnl: float, entry: float, qty: float, leverage: int) -> float:
    notional = entry * qty
    if notional <= 0 or leverage <= 0:
        return 0.0
    return pnl / (notional / leverage) * 100.0


def get_engine_mode(request) -> str:
    engine = getattr(request.app.state, "bot_engine", None)
    return getattr(engine, "_mode", "paper") if engine is not None else "paper"


def _get_binance(request):
    """The engine's authenticated BinanceService (creds live in its instance,
    loaded from the Account table at startup). A fresh service would 401 on
    signed endpoints like positionRisk — only its public endpoints work."""
    engine = getattr(request.app.state, "bot_engine", None)
    binance = getattr(engine, "binance", None) if engine is not None else None
    if binance is not None:
        return binance
    from backend.services.binance_service import BinanceService
    return BinanceService()


async def fetch_pricing(request, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Per-symbol pricing for open positions.

    Returns {symbol: {entry_price, quantity, leverage, mark_price,
    unrealized_pnl, live: bool}}. Live mode queries positionRisk once; any
    failure falls back to mark-price computation (live: False).
    """
    out: dict[str, dict[str, Any]] = {}
    binance = _get_binance(request)

    if get_engine_mode(request) == "live":
        try:
            rows = await binance.get_all_positions()  # non-zero positions only
            for p in rows:
                out[p["symbol"]] = {
                    "entry_price": float(p.get("entryPrice", 0) or 0),
                    "quantity": float(p.get("quantity", 0) or 0),
                    "leverage": int(p.get("leverage", 1) or 1),
                    "mark_price": float(p.get("markPrice", 0) or 0),
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0) or 0),
                    "live": True,
                }
        except Exception as e:
            logger.warning(f"positionRisk fetch failed, falling back to mark price: {e}")

    for s in set(symbols) - set(out):
        try:
            mark = float((await binance.get_mark_price(s))["markPrice"])
        except Exception:
            mark = 0.0
        out[s] = {"mark_price": mark, "live": False}
    return out


def apply_pricing(pos, pricing: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Resolve (entry, quantity, leverage, mark_price, unrealized_pnl, roi_pct)
    for one position row, preferring the exchange's numbers in live mode."""
    side = pos.side
    entry = float(pos.entry_price)
    qty = float(pos.quantity)
    leverage = int(pos.leverage)
    p = pricing.get(pos.symbol, {})

    if p.get("live"):
        entry = float(p.get("entry_price") or entry)
        qty = float(p.get("quantity") or qty)
        leverage = int(p.get("leverage") or leverage)
        mark = float(p.get("mark_price") or 0.0)
        pnl = float(p.get("unrealized_pnl") or 0.0)
        if mark <= 0:  # exchange row without a mark price — derive it
            mark = entry - (pnl / qty if qty else 0.0) * (1 if side == "LONG" else -1)
    else:
        mark = float(p.get("mark_price", 0.0) or 0.0)
        direction = 1.0 if side == "LONG" else -1.0
        pnl = (mark - entry) * qty * direction if mark > 0 else 0.0

    return {
        "entry_price": entry,
        "quantity": qty,
        "leverage": leverage,
        "mark_price": mark,
        "unrealized_pnl": pnl,
        "unrealized_pnl_pct": _roi_pct(pnl, entry, qty, leverage),
    }
