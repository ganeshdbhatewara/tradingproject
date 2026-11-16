import json
import pytest
from papertrade.engine import (Costs, Portfolio, charge_funding, equity, load_portfolio,
                               close_position, check_liquidations, liquidation_price, open_position,
                               risk_status, save_portfolio, select_strategy, slipped_price, RiskLimitError)


def test_books_are_separate_and_persist(tmp_path):
    p = Portfolio()
    marks = {"BTC/INR": 1_000_000}
    spot = open_position(p, "BTC/INR", "spot", "long", 1000, 1_000_000, mark_prices=marks)
    fut = open_position(p, "BTC/INR", "futures_hedge", "short", 1000, 1_000_000, hedge_ratio=.5, mark_prices=marks)
    assert spot.book == "spot" and fut.book == "futures_hedge"
    path = tmp_path / "state.json"
    save_portfolio(p, path)
    restored = load_portfolio(path)
    assert [x.book for x in restored.positions] == ["spot", "futures_hedge"]
    assert len(restored.trades) == 2


def test_position_cap_and_invalid_short_spot():
    p = Portfolio()
    x = open_position(p, "ETH/INR", "spot", "long", 100_000, 200_000, mark_prices={"ETH/INR": 200_000})
    assert x.quantity * x.entry_price <= 4000
    with pytest.raises(ValueError):
        open_position(p, "ETH/INR", "spot", "short", 1000, 200_000, mark_prices={"ETH/INR": 200_000})


def test_costs_funding_and_liquidation():
    p = Portfolio()
    pos = open_position(p, "BTC/INR", "futures_hedge", "short", 1000, 1_000_000,
                        Costs(fee_rate=.001, slippage_bps=0), hedge_ratio=1, mark_prices={"BTC/INR": 1_000_000})
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
                        Costs(slippage_bps=0), hedge_ratio=1, mark_prices={"BTC/INR": 100_000})
    entry_equity = equity(p, {"BTC/INR": pos.entry_price})
    assert equity(p, {"BTC/INR": pos.entry_price * 1.1}) < entry_equity
    assert equity(p, {"BTC/INR": pos.entry_price * 0.9}) > entry_equity
    with pytest.raises(ValueError):
        open_position(p, "BTC/INR", "unexpected", "short", 1000, 100_000, mark_prices={"BTC/INR": 100_000})
    with pytest.raises(ValueError):
        open_position(p, "BTC/INR", "spot", "long", float("nan"), 100_000, mark_prices={"BTC/INR": 100_000})
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


def test_close_realizes_pnl_and_writes_tax_ledger():
    p = Portfolio()
    pos = open_position(p, "BTC/INR", "spot", "long", 1000, 100_000,
                        Costs(fee_rate=0, slippage_bps=0), mark_prices={"BTC/INR": 100_000})
    event = close_position(p, pos.position_id, 110_000, Costs(fee_rate=0, slippage_bps=0))
    assert event["realized_pnl_inr"] == pytest.approx(100)
    assert event["tax_due_inr"] is None
    assert not p.positions and p.tax_ledger[-1]["position_id"] == pos.position_id
    assert p.cash_inr == pytest.approx(20_100)


def test_daily_loss_circuit_breaker_blocks_new_entries():
    p = Portfolio()
    costs = Costs(slippage_bps=0, max_daily_loss_fraction=.03)
    pos = open_position(p, "BTC/INR", "futures_hedge", "short", 4000, 100_000,
                        costs, mark_prices={"BTC/INR": 100_000})
    marks = {"BTC/INR": pos.entry_price * 1.2}
    assert risk_status(p, marks, costs)["halted"]
    with pytest.raises(RiskLimitError):
        open_position(p, "ETH/INR", "spot", "long", 1000, 200_000,
                      costs, mark_prices={**marks, "ETH/INR": 200_000})


def test_liquidation_closes_short_and_limits_additional_loss():
    p = Portfolio()
    costs = Costs(slippage_bps=0, fee_rate=0)
    pos = open_position(p, "ETH/INR", "futures_hedge", "short", 1000, 100_000,
                        costs, mark_prices={"ETH/INR": 100_000})
    liq = liquidation_price(pos, costs.maintenance_margin_rate)
    events = check_liquidations(p, {"ETH/INR": liq}, costs)
    assert len(events) == 1 and events[0]["reason"] == "simulated_liquidation"
    assert not p.positions


def test_old_position_state_migrates_with_position_id():
    p = Portfolio.from_dict({"cash_inr": 10000, "positions": [{"symbol": "BTC/INR", "book": "spot",
        "quantity": .01, "entry_price": 100000, "opened_at": "2026-01-01T00:00:00+00:00"}]})
    assert p.positions[0].position_id and p.positions[0].entry_fee_inr == 0


def test_funding_updates_trade_journal_with_signed_cashflow():
    p = Portfolio()
    pos = open_position(p, "BTC/INR", "futures_hedge", "short", 1000, 100000,
                        Costs(slippage_bps=0), mark_prices={"BTC/INR": 100000})
    debit = charge_funding(p, {"BTC/INR": 100000}, rate_per_8h=-.001)
    assert debit == pytest.approx(1)
    assert p.trades[-1]["funding_inr"] == pytest.approx(1)
    assert p.trades[-1]["position_id"] == pos.position_id
    charge_funding(p, {"BTC/INR": 100000}, rate_per_8h=.001, event_id="period-1")
    with pytest.raises(ValueError, match="already been settled"):
        charge_funding(p, {"BTC/INR": 100000}, rate_per_8h=.001, event_id="period-1")
