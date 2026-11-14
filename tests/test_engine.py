import json
import pytest
from papertrade.engine import (Costs, Portfolio, charge_funding, equity, load_portfolio,
                               liquidation_price, open_position, save_portfolio, select_strategy,
                               slipped_price)


def test_books_are_separate_and_persist(tmp_path):
    p = Portfolio()
    spot = open_position(p, "BTC/INR", "spot", "long", 1000, 1_000_000)
    fut = open_position(p, "BTC/INR", "futures_hedge", "short", 1000, 1_000_000, hedge_ratio=.5)
    assert spot.book == "spot" and fut.book == "futures_hedge"
    path = tmp_path / "state.json"
    save_portfolio(p, path)
    restored = load_portfolio(path)
    assert [x.book for x in restored.positions] == ["spot", "futures_hedge"]
    assert len(restored.trades) == 2


def test_position_cap_and_invalid_short_spot():
    p = Portfolio()
    x = open_position(p, "ETH/INR", "spot", "long", 100_000, 200_000)
    assert x.quantity * x.entry_price <= 4000
    with pytest.raises(ValueError):
        open_position(p, "ETH/INR", "spot", "short", 1000, 200_000)


def test_costs_funding_and_liquidation():
    p = Portfolio()
    pos = open_position(p, "BTC/INR", "futures_hedge", "short", 1000, 1_000_000,
                        Costs(fee_rate=.001, slippage_bps=0), hedge_ratio=1)
    before = p.cash_inr
    # A positive funding rate pays simulated shorts; a negative one charges them.
    assert charge_funding(p, {"BTC/INR": 1_000_000}, periods=2, rate_per_8h=.001) == pytest.approx(-2)
    assert p.cash_inr == pytest.approx(before + 2)
    assert charge_funding(p, {"BTC/INR": 1_000_000}, periods=2, rate_per_8h=-.001) == pytest.approx(2)
    assert p.cash_inr == pytest.approx(before)
    assert liquidation_price(pos) > pos.entry_price
    assert slipped_price(100, "buy", 10) > 100


def test_short_mark_to_market_and_reject_invalid_inputs():
    p = Portfolio()
    pos = open_position(p, "BTC/INR", "futures_hedge", "short", 1000, 100_000,
                        Costs(slippage_bps=0), hedge_ratio=1)
    entry_equity = equity(p, {"BTC/INR": pos.entry_price})
    assert equity(p, {"BTC/INR": pos.entry_price * 1.1}) < entry_equity
    assert equity(p, {"BTC/INR": pos.entry_price * 0.9}) > entry_equity
    with pytest.raises(ValueError):
        open_position(p, "BTC/INR", "unexpected", "short", 1000, 100_000)
    with pytest.raises(ValueError):
        open_position(p, "BTC/INR", "spot", "long", float("nan"), 100_000)
    with pytest.raises(ValueError):
        charge_funding(p, {}, rate_per_8h=.001)


def test_strategy_explains_and_manual_choice():
    assert "reason" in select_strategy("trending", .02, .01)
    result = select_strategy("ranging", 0, 0, "Breakout")
    assert result["strategy"] == "Breakout" and "manually" in result["reason"]


def test_state_json_is_valid_and_rejects_nan(tmp_path):
    path = tmp_path / "state.json"
    p = Portfolio()
    save_portfolio(p, path)
    json.loads(path.read_text())
    p.cash_inr = float("nan")
    with pytest.raises(ValueError):
        save_portfolio(p, path)
