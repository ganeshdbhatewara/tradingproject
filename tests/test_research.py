import pytest
from papertrade.backtest import (backtest_ohlc, detect_candle_patterns,
                                 strategy_signal, walk_forward_returns)
from papertrade.market_data import futures_data_notice, futures_snapshot, spot_ohlc


def _rows(n=100):
    rows = []
    for i in range(n):
        close = 100 + i * 0.5
        rows.append([i * 60_000, close - .2, close + .5, close - .5, close])
    return rows


def test_patterns_detect_doji_hammer_and_engulfing():
    candles = [[1, 100, 110, 90, 101], [2, 105, 106, 95, 104],
               [3, 105, 106, 99, 100], [4, 99, 107, 98, 106]]
    found = detect_candle_patterns(candles)
    names = {p["pattern"] for r in found for p in r["patterns"]}
    assert {"doji", "hammer", "bullish_engulfing"}.issubset(names)


def test_strategy_signals_and_backtest_are_bounded_and_net_of_costs():
    rows = _rows()
    closes = [r[4] for r in rows]
    assert strategy_signal("Trend following", closes) == 1
    assert strategy_signal("Momentum", closes) == 1
    assert strategy_signal("Breakout", [100] * 20 + [101]) == 1
    assert strategy_signal("Mean reversion", [100] * 20 + [120]) == -1
    result = backtest_ohlc(rows, "Trend following", fee_rate=.001, slippage_bps=10)
    no_cost = backtest_ohlc(rows, "Trend following", fee_rate=0, slippage_bps=0)
    assert result["initial_capital_inr"] == 20_000
    assert result["trade_count"] >= 1
    assert result["final_equity_inr"] < no_cost["final_equity_inr"]
    assert result["out_of_sample_return"] == pytest.approx(result["out_of_sample_return"])
    assert "no leverage" in result["assumptions"]
    with pytest.raises(ValueError):
        backtest_ohlc(rows[:20], "Momentum")


def test_backtest_closes_a_position_when_trend_reverses():
    rows = []
    for i in range(120):
        close = 100 + i if i < 50 else 150 - 2 * (i - 50)
        rows.append([i * 60_000, close, close + 1, close - 1, close])
    result = backtest_ohlc(rows, "Trend following", fee_rate=0, slippage_bps=0)
    assert result["trade_count"] >= 2
    assert {trade["side"] for trade in result["trades"]} == {"long", "short"}


def test_market_adapters_keep_actual_futures_data_labeled(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"symbol": "BTCUSDT", "markPrice": "50000", "indexPrice": "49900",
                    "lastFundingRate": "0.0001", "nextFundingTime": "123", "time": "456"}
    monkeypatch.setattr("papertrade.market_data.requests.get", lambda *a, **k: Response())
    result = futures_snapshot("BTC/INR")
    assert result["source_type"] == "actual_exchange_futures"
    assert result["quote_currency"] == "USDT" and result["mark_price_usdt"] == 50000
    assert futures_data_notice()["configured"]
    with pytest.raises(ValueError):
        spot_ohlc("XRP/INR")


def test_futures_adapter_falls_back_when_binance_geo_blocks(monkeypatch):
    class Response:
        def __init__(self, data, status=200): self.data, self.status = data, status
        def raise_for_status(self):
            if self.status >= 400: raise RuntimeError(f"HTTP {self.status}")
        def json(self): return self.data
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if "binance" in url: return Response({}, 451)
        return Response({"retCode": 0, "time": 456, "result": {"list": [{
            "symbol": "ETHUSDT", "markPrice": "2000", "indexPrice": "1990",
            "fundingRate": "-0.0001", "nextFundingTime": "789"}]}})
    monkeypatch.setattr("papertrade.market_data.requests.get", get)
    result = futures_snapshot("ETH/INR")
    assert result["exchange"] == "Bybit linear perpetual"
    assert result["funding_rate"] == pytest.approx(-.0001)
    assert len(calls) == 2 and "bybit" in calls[1]
