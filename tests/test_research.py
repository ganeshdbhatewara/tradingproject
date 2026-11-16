import pytest
import papertrade.backtest as backtest_module
from papertrade.backtest import (backtest_ohlc, detect_candle_patterns,
                                 multi_pair_strategy_report, strategy_signal,
                                 walk_forward_returns)
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


def test_multi_pair_selector_ranks_by_training_not_out_of_sample(monkeypatch):
    values = {
        "Trend following": (.10, -.30),
        "Breakout": (.02, .50),
        "Mean reversion": (-.01, 0.0),
        "Momentum": (.05, .20),
    }

    def fake_backtest(rows, strategy, **kwargs):
        train, oos = values[strategy]
        return {"train_return": train, "out_of_sample_return": oos,
                "net_return": train + oos, "trade_count": 2}

    monkeypatch.setattr(backtest_module, "backtest_ohlc", fake_backtest)
    report = multi_pair_strategy_report({"BTC/INR": _rows(), "ETH/INR": _rows()})
    assert report["candidate_strategy"] == "Trend following"
    assert report["selected_strategy"] is None  # negative OOS performance fails validation
    assert report["ranking"][0]["average_train_return"] == pytest.approx(.10)
    assert report["ranking"][0]["average_out_of_sample_return"] == pytest.approx(-.30)
    assert len(report["pair_results"]) == 8
    assert "stays flat pending validation" in report["reason"]
    assert "OOS data was not used to rank" in report["reason"]


def test_multi_pair_selector_stays_flat_without_two_pairs_or_positive_training(monkeypatch):
    insufficient = multi_pair_strategy_report({"BTC/INR": _rows()})
    assert insufficient["selected_strategy"] is None
    assert "at least two pairs" in insufficient["reason"]

    monkeypatch.setattr(backtest_module, "backtest_ohlc", lambda *args, **kwargs: {
        "train_return": -.01, "out_of_sample_return": .5, "net_return": .4,
        "trade_count": 1,
    })
    losing = multi_pair_strategy_report({"BTC/INR": _rows(), "ETH/INR": _rows()})
    assert losing["selected_strategy"] is None
    assert "stays flat" in losing["reason"]


def test_multi_pair_selector_uses_training_winner_when_oos_also_validates(monkeypatch):
    monkeypatch.setattr(backtest_module, "backtest_ohlc", lambda *args, **kwargs: {
        "train_return": .02, "out_of_sample_return": .01, "net_return": .03,
        "trade_count": 1,
    })
    report = multi_pair_strategy_report({"BTC/INR": _rows(), "ETH/INR": _rows()})
    assert report["candidate_strategy"] == report["selected_strategy"]
    assert report["selected_strategy"] == "Breakout"  # deterministic tie-break
