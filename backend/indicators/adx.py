"""Average Directional Index (ADX) — trend strength regardless of direction."""
from typing import Optional


def calculate_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Optional[dict]:
    """Calculate ADX and related indicators.
    
    Returns dict with:
    - adx: Average Directional Index (0-100)
    - plus_di: Positive Directional Index (bullish pressure)
    - minus_di: Negative Directional Index (bearish pressure)
    - adx_status: 'STRONG_TREND' if ADX > 25, 'WEAK_TREND' if 20-25, 'RANGING' if < 20
    """
    if len(highs) < period * 2 + 1:
        return None
    
    # Calculate True Range
    tr_list = []
    for i in range(1, len(highs)):
        h = highs[i]
        l = lows[i]
        c_prev = closes[i - 1]
        tr = max(
            h - l,
            abs(h - c_prev),
            abs(l - c_prev)
        )
        tr_list.append(tr)
    
    # Calculate +DM and -DM
    dm_list = []
    for i in range(1, len(highs)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        
        if up_move > down_move and up_move > 0:
            plus_dm = up_move
        else:
            plus_dm = 0
            
        if down_move > up_move and down_move > 0:
            minus_dm = down_move
        else:
            minus_dm = 0
        
        dm_list.append((plus_dm, minus_dm))
    
    # Smoothed averages (Wilder's smoothing)
    def wilder_smooth(values: list[float], period: int) -> list[float]:
        if not values:
            return []
        result = [0.0] * (len(values) - period) + [sum(values[:period]) / period]
        for i in range(period, len(values)):
            result.append((result[-1] * (period - 1) + values[i]) / period)
        return result
    
    tr_smooth = wilder_smooth(tr_list, period)
    plus_dm_smooth = wilder_smooth([d[0] for d in dm_list], period)
    minus_dm_smooth = wilder_smooth([d[1] for d in dm_list], period)
    
    # Calculate +DI and -DI
    plus_di = [(x / y * 100) if y > 0 else 0 for x, y in zip(plus_dm_smooth, tr_smooth)]
    minus_di = [(x / y * 100) if y > 0 else 0 for x, y in zip(minus_dm_smooth, tr_smooth)]
    
    # Calculate DX
    dx = []
    for p_d, m_d in zip(plus_di, minus_di):
        total = p_d + m_d
        dx.append((abs(p_d - m_d) / total * 100) if total > 0 else 0)
    
    # Smooth DX to get ADX
    adx = wilder_smooth(dx, period)
    
    # Get latest values
    latest_adx = adx[-1] if adx else 0
    latest_plus_di = plus_di[-1] if plus_di else 0
    latest_minus_di = minus_di[-1] if minus_di else 0
    
    # Classify trend strength
    if latest_adx > 25:
        adx_status = "STRONG_TREND"
    elif latest_adx >= 20:
        adx_status = "WEAK_TREND"
    else:
        adx_status = "RANGING"
    
    return {
        "adx": latest_adx,
        "plus_di": latest_plus_di,
        "minus_di": latest_minus_di,
        "adx_status": adx_status,
        "adx_series": adx[-20:] if len(adx) >= 20 else adx,  # For visualization
    }


def get_market_regime(adx_data: dict, price: float, sma_50: float) -> str:
    """Classify market regime combining ADX and price position.
    
    Regimes:
    - STRONG_BULL: ADX > 25, price > SMA50, +DI > -DI
    - STRONG_BEAR: ADX > 25, price < SMA50, -DI > +DI
    - WEAK_TREND: ADX 20-25 (any direction)
    - RANGING: ADX < 20 (any direction)
    """
    if not adx_data:
        return "UNKNOWN"
    
    adx = adx_data.get("adx", 0)
    plus_di = adx_data.get("plus_di", 0)
    minus_di = adx_data.get("minus_di", 0)
    
    price_above_sma = price > sma_50 if sma_50 else False
    bull_direction = plus_di > minus_di
    
    if adx < 20:
        return "RANGING"
    elif adx > 25 and price_above_sma and bull_direction:
        return "STRONG_BULL"
    elif adx > 25 and not price_above_sma and not bull_direction:
        return "STRONG_BEAR"
    elif adx > 20:
        return "WEAK_TREND"
    else:
        return "RANGING"
