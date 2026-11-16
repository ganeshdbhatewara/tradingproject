"""Deterministic simulated trading primitives with separate position books."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
import json
import math
import os
import tempfile
from typing import Literal
from uuid import uuid4

INITIAL_INR = 20_000.0
SYMBOLS = ("BTC/INR", "ETH/INR")
Book = Literal["spot", "short", "futures_hedge"]


@dataclass
class Position:
    position_id: str
    symbol: str
    book: Book
    quantity: float
    entry_price: float
    opened_at: str
    margin_inr: float = 0.0
    entry_fee_inr: float = 0.0
    funding_inr: float = 0.0


@dataclass
class Portfolio:
    cash_inr: float = INITIAL_INR
    positions: list[Position] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    tax_ledger: list[dict] = field(default_factory=list)
    peak_equity_inr: float = INITIAL_INR
    daily_start_equity_inr: float = INITIAL_INR
    daily_start_date: str = field(default_factory=lambda: date.today().isoformat())
    funding_events: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "Portfolio":
        positions = []
        for source in raw.get("positions", []):
            item = dict(source)
            item.setdefault("position_id", str(uuid4()))
            item.setdefault("entry_fee_inr", 0.0)
            item.setdefault("funding_inr", 0.0)
            positions.append(Position(**item))
        return cls(
            cash_inr=float(raw.get("cash_inr", INITIAL_INR)),
            positions=positions,
            trades=list(raw.get("trades", [])), tax_ledger=list(raw.get("tax_ledger", [])),
            peak_equity_inr=float(raw.get("peak_equity_inr", INITIAL_INR)),
            daily_start_equity_inr=float(raw.get("daily_start_equity_inr", INITIAL_INR)),
            daily_start_date=str(raw.get("daily_start_date", date.today().isoformat())),
            funding_events=list(raw.get("funding_events", [])),
        )

    def to_dict(self) -> dict:
        return {"cash_inr": self.cash_inr, "positions": [asdict(p) for p in self.positions],
                "trades": self.trades, "tax_ledger": self.tax_ledger,
                "peak_equity_inr": self.peak_equity_inr,
                "daily_start_equity_inr": self.daily_start_equity_inr,
                "daily_start_date": self.daily_start_date,
                "funding_events": self.funding_events}


@dataclass(frozen=True)
class Costs:
    fee_rate: float = 0.001
    slippage_bps: float = 10.0
    funding_rate_8h: float = 0.0001
    maintenance_margin_rate: float = 0.05
    max_position_fraction: float = 0.20
    max_daily_loss_fraction: float = 0.03
    max_drawdown_fraction: float = 0.20

    def validate(self) -> None:
        if not 0 <= self.fee_rate <= 0.1:
            raise ValueError("Fee rate must be between 0 and 10%.")
        if not math.isfinite(self.slippage_bps) or not 0 <= self.slippage_bps < 10_000:
            raise ValueError("Slippage bps must be finite and in [0, 10000).")
        if not 0 < self.maintenance_margin_rate < 1:
            raise ValueError("Maintenance margin rate must be between 0 and 1.")
        if not 0 < self.max_position_fraction <= 1:
            raise ValueError("Position fraction must be in (0, 1].")
        if not 0 < self.max_daily_loss_fraction <= 1:
            raise ValueError("Daily loss fraction must be in (0, 1].")
        if not 0 < self.max_drawdown_fraction <= 1:
            raise ValueError("Maximum drawdown fraction must be in (0, 1].")


def load_portfolio(path: str | Path) -> Portfolio:
    p = Path(path)
    if not p.exists():
        return Portfolio()
    with p.open(encoding="utf-8") as f:
        return Portfolio.from_dict(json.load(f))


def save_portfolio(portfolio: Portfolio, path: str | Path) -> None:
    """Atomic replace prevents a partially-written portfolio on worker interruption."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name, dir=p.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(portfolio.to_dict(), f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def slipped_price(price: float, side: Literal["buy", "sell"], bps: float) -> float:
    if side not in ("buy", "sell") or not math.isfinite(price) or not math.isfinite(bps) or price <= 0 or bps < 0 or bps >= 10_000:
        raise ValueError("Price must be positive and slippage bps in [0, 10000).")
    return price * (1 + bps / 10_000 if side == "buy" else 1 - bps / 10_000)


def equity(portfolio: Portfolio, prices: dict[str, float]) -> float:
    value = portfolio.cash_inr
    for p in portfolio.positions:
        mark = prices[p.symbol]
        if p.book == "spot":
            value += p.quantity * mark
        else:
            # Both non-spot books represent short exposure: rising marks lose value.
            value += p.margin_inr + p.quantity * (p.entry_price - mark)
    return value


class RiskLimitError(ValueError):
    """An order was blocked by a paper-portfolio risk limit."""


def risk_status(portfolio: Portfolio, prices: dict[str, float], costs: Costs = Costs(),
                today: date | None = None) -> dict:
    """Update daily baseline and evaluate a daily loss and peak drawdown circuit breaker."""
    costs.validate()
    required = {p.symbol for p in portfolio.positions}
    if not required.issubset(prices) or any(not math.isfinite(prices[s]) or prices[s] <= 0 for s in required):
        raise ValueError("Valid marks are required for every open position")
    now = today or date.today()
    marked = equity(portfolio, prices)
    if portfolio.daily_start_date != now.isoformat():
        portfolio.daily_start_date = now.isoformat()
        portfolio.daily_start_equity_inr = marked
    portfolio.peak_equity_inr = max(portfolio.peak_equity_inr, marked)
    day_loss = max(0.0, (portfolio.daily_start_equity_inr - marked) /
                   max(portfolio.daily_start_equity_inr, 1e-9))
    drawdown = max(0.0, (portfolio.peak_equity_inr - marked) /
                   max(portfolio.peak_equity_inr, 1e-9))
    return {"equity_inr": marked, "daily_loss_fraction": day_loss,
            "drawdown_fraction": drawdown,
            "daily_limit_fraction": costs.max_daily_loss_fraction,
            "drawdown_limit_fraction": costs.max_drawdown_fraction,
            "halted": day_loss >= costs.max_daily_loss_fraction or drawdown >= costs.max_drawdown_fraction}


def open_position(portfolio: Portfolio, symbol: str, book: Book, side: Literal["long", "short"],
                  notional_inr: float, market_price: float, costs: Costs = Costs(),
                  hedge_ratio: float = 1.0, source: str = "market",
                  mark_prices: dict[str, float] | None = None) -> Position:
    """Open a paper position. Short and futures books are always simulated."""
    if symbol not in SYMBOLS or book not in ("spot", "short", "futures_hedge") or side not in ("long", "short"):
        raise ValueError("Unsupported symbol or side")
    costs.validate()
    if not all(math.isfinite(v) for v in (notional_inr, market_price, hedge_ratio)):
        raise ValueError("Notional, price, and hedge ratio must be finite")
    if not (0 <= hedge_ratio <= 1):
        raise ValueError("Hedge ratio must be between 0 and 1")
    if notional_inr <= 0 or market_price <= 0:
        raise ValueError("Notional and price must be positive")
    if (book == "spot" and side != "long") or (book != "spot" and side != "short"):
        raise ValueError("Spot is long-only; short and futures books simulate short exposure")
    if mark_prices is None:
        raise ValueError("Current marks are required for portfolio risk checks")
    status = risk_status(portfolio, mark_prices, costs)
    if status["halted"]:
        raise RiskLimitError("Daily loss or peak drawdown limit reached; new positions are halted")
    px = slipped_price(market_price, "buy" if side == "long" else "sell", costs.slippage_bps)
    maximum = max(0.0, portfolio.cash_inr) * costs.max_position_fraction
    if book == "futures_hedge":
        notional_inr *= hedge_ratio
    notional_inr = min(notional_inr, maximum)
    if notional_inr <= 0:
        raise ValueError("No available notional remains under the portfolio cap")
    fee = notional_inr * costs.fee_rate
    margin = notional_inr if book != "spot" else 0.0
    debit = (notional_inr + fee) if book == "spot" else (margin + fee)
    if debit > portfolio.cash_inr:
        raise ValueError("Insufficient available cash/margin")
    portfolio.cash_inr -= debit
    pos = Position(str(uuid4()), symbol, book, notional_inr / px, px,
                   datetime.now(timezone.utc).isoformat(), margin, fee)
    portfolio.positions.append(pos)
    portfolio.trades.append({"time": pos.opened_at, "symbol": symbol, "book": book,
        "side": side, "quantity": pos.quantity, "price_inr": px, "fee_inr": fee,
        "funding_inr": 0.0, "slippage_bps": costs.slippage_bps, "source": source,
        "hedge_ratio": hedge_ratio if book == "futures_hedge" else None,
        "position_id": pos.position_id, "status": "open"})
    return pos


def close_position(portfolio: Portfolio, position_id: str, market_price: float,
                   costs: Costs = Costs(), fraction: float = 1.0,
                   reason: str = "manual") -> dict:
    """Close part/all of a position, release its margin and append realized P&L/tax-ledger data."""
    costs.validate()
    if not math.isfinite(market_price) or market_price <= 0 or not 0 < fraction <= 1:
        raise ValueError("Close price must be positive and fraction must be in (0, 1].")
    pos = next((x for x in portfolio.positions if x.position_id == position_id), None)
    if pos is None:
        raise ValueError("Position not found")
    close_side = "sell" if pos.book == "spot" else "buy"
    px = slipped_price(market_price, close_side, costs.slippage_bps)
    qty = pos.quantity * fraction
    entry_fee = pos.entry_fee_inr * fraction
    close_fee = qty * px * costs.fee_rate
    entry_value = qty * pos.entry_price
    close_value = qty * px
    gross = close_value - entry_value if pos.book == "spot" else entry_value - close_value
    funding = pos.funding_inr * fraction
    pnl = gross - entry_fee - close_fee - funding
    margin = pos.margin_inr * fraction
    if pos.book == "spot":
        portfolio.cash_inr += close_value - close_fee
        proceeds, basis = close_value, entry_value
    else:
        portfolio.cash_inr += margin + gross - close_fee
        proceeds, basis = entry_value, close_value
    event = {"time": datetime.now(timezone.utc).isoformat(), "position_id": position_id,
             "symbol": pos.symbol, "book": pos.book, "quantity": qty,
             "entry_price_inr": pos.entry_price, "exit_price_inr": px,
             "proceeds_inr": proceeds, "cost_basis_inr": basis,
             "entry_fee_inr": entry_fee, "exit_fee_inr": close_fee,
             "funding_inr": funding, "realized_pnl_inr": pnl, "tax_due_inr": None,
             "tax_note": "Tax due not calculated; ledger values are informational only.",
             "reason": reason}
    portfolio.tax_ledger.append(event)
    portfolio.trades.append({**event, "side": "close", "status": "closed"})
    for trade in portfolio.trades:
        if trade.get("position_id") == position_id and trade.get("side") in ("long", "short"):
            trade["status"] = "closed" if fraction == 1 else "partially_closed"
    if fraction == 1:
        portfolio.positions.remove(pos)
    else:
        pos.quantity *= 1 - fraction
        pos.margin_inr *= 1 - fraction
        pos.entry_fee_inr *= 1 - fraction
        pos.funding_inr *= 1 - fraction
    return event


def check_liquidations(portfolio: Portfolio, mark_prices: dict[str, float],
                       costs: Costs = Costs()) -> list[dict]:
    """Close simulated short positions once marks cross their conservative estimate."""
    closed = []
    for pos in list(portfolio.positions):
        if pos.book == "spot":
            continue
        if pos.symbol not in mark_prices:
            raise ValueError(f"Missing liquidation mark for {pos.symbol}")
        threshold = liquidation_price(pos, costs.maintenance_margin_rate)
        if mark_prices[pos.symbol] >= threshold:
            closed.append(close_position(portfolio, pos.position_id, mark_prices[pos.symbol],
                                         costs, reason="simulated_liquidation"))
    return closed


def charge_funding(portfolio: Portfolio, mark_prices: dict[str, float], periods: int = 1,
                   rate_per_8h: float | dict[str, float] = 0.0001,
                   event_id: str | None = None) -> float:
    """Apply signed funding to short futures: positive rates credit shorts, negative debit them.

    Returns funding cost (positive means cash debit; negative means cash credit).
    """
    if not isinstance(periods, int) or periods < 0 or (isinstance(rate_per_8h, (int, float)) and not math.isfinite(rate_per_8h)):
        raise ValueError("Periods must be nonnegative and funding rate finite")
    if event_id and event_id in portfolio.funding_events:
        raise ValueError("This funding event has already been settled")
    rates = rate_per_8h if isinstance(rate_per_8h, dict) else {}
    for p in portfolio.positions:
        if p.book == "futures_hedge" and (p.symbol not in mark_prices or
                not math.isfinite(mark_prices[p.symbol]) or mark_prices[p.symbol] <= 0 or
                (isinstance(rate_per_8h, dict) and p.symbol not in rates)):
            raise ValueError("Valid marks and funding rates are required for every futures position")
        if p.book == "futures_hedge":
            rate = rates[p.symbol] if isinstance(rate_per_8h, dict) else rate_per_8h
            if not math.isfinite(rate):
                raise ValueError("Funding rates must be finite")
    by_id = {}
    for p in portfolio.positions:
        if p.book == "futures_hedge":
            rate = rates[p.symbol] if isinstance(rate_per_8h, dict) else rate_per_8h
            by_id[p.position_id] = -p.quantity * mark_prices[p.symbol] * rate * periods
            p.funding_inr += by_id[p.position_id]
    cost = sum(by_id.values())
    portfolio.cash_inr -= cost
    for trade in portfolio.trades:
        if trade.get("position_id") in by_id:
            trade["funding_inr"] = float(trade.get("funding_inr", 0.0)) + by_id[trade["position_id"]]
    if event_id:
        portfolio.funding_events.append(event_id)
    return cost


def liquidation_price(position: Position, maintenance_rate: float = 0.05) -> float | None:
    if position.book == "spot":
        return None
    # Isolated-margin short approximation, with zero fees; not an exchange contract model.
    if position.quantity <= 0 or not (0 < maintenance_rate < 1):
        raise ValueError("Invalid quantity or maintenance rate")
    return position.entry_price * (1 + position.margin_inr /
                                   (position.quantity * position.entry_price) - maintenance_rate)


def select_strategy(regime: str, trend: float, volatility: float,
                    manual: str = "Auto") -> dict:
    """Transparent, deterministic selector; indicators are caller-supplied."""
    names = {"Trend following", "Breakout", "Mean reversion", "Momentum"}
    if manual != "Auto":
        if manual not in names:
            raise ValueError("Unknown strategy")
        return {"strategy": manual, "reason": "Selected manually by the user."}
    if regime == "trending" and abs(trend) > 0.01:
        strategy, why = "Trend following", "Trending regime with directional trend strength above 1%."
    elif regime == "volatile" and volatility > 0.03:
        strategy, why = "Breakout", "High volatility regime favors confirmed range breaks."
    elif regime == "ranging":
        strategy, why = "Mean reversion", "Range-bound regime favors moves back toward the mean."
    else:
        strategy, why = "Momentum", "No strong trend or range signal; use momentum confirmation."
    return {"strategy": strategy, "reason": why}
