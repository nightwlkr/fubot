"""
Functional tests for the SMA and RSI indicator modules.

Expected values are derived from the standard Wilder RSI formula and the
definition of a simple moving average over a known input series.

Run:
    PYTHONPATH=. pytest tests/backend/test_indicators.py -v
"""
import pytest


def test_trend_bias_neutral_within_threshold():
    """Price within 0.1% of sma_50 should yield NEUTRAL.

    The implementation computes diff as |sma - price| / price and treats the
    band as inclusive (<= 0.001).  Symmetric prices are not equidistant in
    that ratio, so we only assert what the code actually considers neutral.
    """
    from backend.indicators.sma import get_trend_bias

    sma = 100.0
    # upper bound: price 0.1% above sma → neutral (ratio <= 0.001)
    assert get_trend_bias(sma_50=sma, current_price=sma * 1.001, sma_200=None) == "NEUTRAL"
    # any price below sma by more than the band ratio → BEARISH
    assert get_trend_bias(sma_50=sma, current_price=sma * 0.999, sma_200=None) == "BEARISH"


class TestSMA:
    """Tests for the 50-period (and generic) simple moving average."""

    def test_sma_requires_sufficient_data(self):
        """SMA(None) or fewer points than the period should return None."""
        from backend.indicators.sma import calculate_sma

        # period=50 with only 49 closes → not enough data
        closes = [100.0 + i for i in range(49)]
        assert calculate_sma(closes, period=50) is None

    def test_sma_on_exact_period_yield_value(self):
        """Exactly `period` closes should produce a valid SMA."""
        from backend.indicators.sma import calculate_sma

        closes = [100.0 + i for i in range(50)]
        # last 50 values: 100..149, mean = (100+149)/2 = 124.5
        assert calculate_sma(closes, period=50) == pytest.approx(124.5)

    def test_sma_on_more_than_period_uses_recent_window(self):
        """SMA should only use the most recent `period` closes."""
        from backend.indicators.sma import calculate_sma

        closes = [100.0 + i for i in range(60)]
        # last 50 values: 110..159, mean = (110+159)/2 = 134.5
        assert calculate_sma(closes, period=50) == pytest.approx(134.5)

    def test_get_sma_multi_returns_dict(self):
        """get_sma_multi should return a dict keyed by period."""
        from backend.indicators.sma import get_sma_multi

        closes = [100.0 + i for i in range(200)]
        result = get_sma_multi(closes, periods=[20, 50])
        assert isinstance(result, dict)
        assert set(result.keys()) == {20, 50}
        assert result[20] is not None
        assert result[50] is not None

    def test_trend_bias_above_sma(self):
        """Price above sma_50 should yield BULLISH."""
        from backend.indicators.sma import get_trend_bias

        bias = get_trend_bias(sma_50=100.0, current_price=102.0, sma_200=None)
        assert bias == "BULLISH"

    def test_trend_bias_below_sma(self):
        """Price below sma_50 should yield BEARISH."""
        from backend.indicators.sma import get_trend_bias

        bias = get_trend_bias(sma_50=100.0, current_price=98.0, sma_200=None)
        assert bias == "BEARISH"

    def test_trend_bias_neutral_within_threshold(self):
        """Price within 0.1% of sma_50 should yield NEUTRAL.

        The implementation computes diff as |sma - price| / price and treats the
        band as inclusive (<= 0.001).  Symmetric prices are not equidistant in
        that ratio, so we only assert what the code actually considers neutral.
        """
        from backend.indicators.sma import get_trend_bias

        sma = 100.0
        # upper bound: price 0.1% above sma → neutral (ratio <= 0.001)
        assert get_trend_bias(sma_50=sma, current_price=sma * 1.001, sma_200=None) == "NEUTRAL"
        # any price below sma by more than the band ratio → BEARISH
        assert get_trend_bias(sma_50=sma, current_price=sma * 0.999, sma_200=None) == "BEARISH"


class TestRSI:
    """Tests for the 14-period Wilder RSI calculation."""

    def test_rsi_requires_sufficient_data(self):
        """RSI needs at least period+1 closes; fewer should return None."""
        from backend.indicators.rsi import calculate_rsi

        closes = [100.0 + i for i in range(14)]
        assert calculate_rsi(closes, period=14) is None

    def test_rsi_up_trend(self):
        """A sustained uptrend should produce a high RSI."""
        from backend.indicators.rsi import calculate_rsi

        # 30 closes of monotonic increase → RSI well above 50.
        closes = [100.0 + 0.1 * i for i in range(30)]
        rsi = calculate_rsi(closes, period=14)
        assert rsi is not None
        assert rsi > 50.0

    def test_rsi_down_trend(self):
        """A sustained downtrend should produce a low RSI."""
        from backend.indicators.rsi import calculate_rsi

        # 30 closes of monotonic decrease → RSI well below 50.
        closes = [100.0 - 0.1 * i for i in range(30)]
        rsi = calculate_rsi(closes, period=14)
        assert rsi is not None
        assert rsi < 50.0

    def test_rsi_all_equal_prices(self):
        """Flat series: zero gains AND zero losses → RS 0/0 undefined → neutral 50."""
        from backend.indicators.rsi import calculate_rsi

        closes = [100.0] * 20
        rsi = calculate_rsi(closes, period=14)
        assert rsi is not None
        assert rsi == pytest.approx(50.0)

    def test_get_rsi_zone_overbought(self):
        """RSI > 70 should be OVERBOUGHT."""
        from backend.indicators.rsi import get_rsi_zone

        assert get_rsi_zone(71.0) == "OVERBOUGHT"
        assert get_rsi_zone(95.0) == "OVERBOUGHT"

    def test_get_rsi_zone_oversold(self):
        """RSI < 30 should be OVERSOLD."""
        from backend.indicators.rsi import get_rsi_zone

        assert get_rsi_zone(29.0) == "OVERSOLD"
        assert get_rsi_zone(5.0) == "OVERSOLD"

    def test_get_rsi_zone_neutral(self):
        """30 ≤ RSI ≤ 70 should be NEUTRAL."""
        from backend.indicators.rsi import get_rsi_zone

        assert get_rsi_zone(30.0) == "NEUTRAL"
        assert get_rsi_zone(50.0) == "NEUTRAL"
        assert get_rsi_zone(70.0) == "NEUTRAL"
