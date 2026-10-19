"""Correlation guard — prevents overexposure to correlated assets.

Added 2026-10-19 as part of fubot profitability fix. Production data showed
positions in BTC, ETH, SOL can all hit SL in the same liquidation wave due to
high correlation (ρ = 0.89-0.90).

This module tracks open positions by correlation group and limits same-direction
exposure to prevent catastrophic simultaneous losses.
"""
import logging
from typing import Optional, Callable, Set

logger = logging.getLogger(__name__)

# Correlation groups based on typical crypto correlations
# When one member moves, others tend to follow within minutes
CORRELATION_GROUPS = {
    "btc_family": {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "MATICUSDT"},
    "defi_family": {"UNIUSDT", "AAVEUSDT", "LINKUSDT", "CRVUSDT", "SUSHIUSDT"},
    "layer1_family": {"ADAUSDT", "SOLUSDT", "AVAXUSDT", "DOTUSDT", "ATOMUSDT"},
    "memes": {"DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT"},
}


class CorrelationGuard:
    """Prevents overexposure to correlated assets in the same direction."""

    def __init__(
        self,
        settings: Optional[dict] = None,
    ):
        self.settings = settings or {}
        # Max same-direction positions per correlation group
        self.max_per_group = int(self.settings.get("max_correlated_positions", 2))

    def _get_group(self, symbol: str) -> Optional[str]:
        """Get the correlation group for a symbol, or None if not grouped."""
        for group_name, symbols in CORRELATION_GROUPS.items():
            if symbol in symbols:
                return group_name
        return None

    def check_correlation(
        self,
        symbol: str,
        direction: str,
        open_positions: dict,
    ) -> tuple[bool, str]:
        """Check if adding this position would exceed correlation limits.

        Args:
            symbol: Trading pair (e.g., "ETHUSDT")
            direction: "LONG" or "SHORT"
            open_positions: Dict of symbol -> position info

        Returns:
            (should_trade, reason)
            - should_trade=True means proceed
            - should_trade=False means skip (with reason)
        """
        group = self._get_group(symbol)

        # If symbol not in any group, no correlation check needed
        if group is None:
            return True, ""

        # Count same-direction positions in this group
        same_direction_count = 0
        same_direction_symbols = []

        for pos_symbol, pos_info in open_positions.items():
            if self._get_group(pos_symbol) == group:
                pos_direction = pos_info.get("side", "")
                if pos_direction == direction:
                    same_direction_count += 1
                    same_direction_symbols.append(pos_symbol)

        logger.debug(
            "Correlation check: %s %s in group '%s' — "
            "same-direction positions: %d/%d (%s)",
            symbol, direction, group,
            same_direction_count, self.max_per_group,
            ", ".join(same_direction_symbols) if same_direction_symbols else "none"
        )

        if same_direction_count >= self.max_per_group:
            return False, (
                f"Correlation limit reached: {same_direction_count}/{self.max_per_group} "
                f"{direction} positions in {group} group ({', '.join(same_direction_symbols)})"
            )

        return True, ""

    def get_correlation_info(self, open_positions: dict) -> dict:
        """Get current correlation exposure across all groups."""
        exposure = {}

        for group_name, symbols in CORRELATION_GROUPS.items():
            longs = sum(1 for s in symbols if open_positions.get(s, {}).get("side") == "LONG")
            shorts = sum(1 for s in symbols if open_positions.get(s, {}).get("side") == "SHORT")
            exposure[group_name] = {
                "longs": longs,
                "shorts": shorts,
                "total": longs + shorts,
                "limit": self.max_per_group,
                "at_limit": longs >= self.max_per_group or shorts >= self.max_per_group,
            }

        return exposure
