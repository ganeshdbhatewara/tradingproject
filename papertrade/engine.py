"""Deterministic simulated trading primitives with separate position books."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
import tempfile
from typing import Literal

INITIAL_INR = 20_000.0
SYMBOLS = ("BTC/INR", "ETH/INR")
Book = Literal["spot", "short", "futures_hedge"]


@dataclass
class Position:
    symbol: str
    book: Book
    quantity: float
    entry_price: float
    opened_at: str
    margin_inr: float = 0.0


@dataclass
class Portfolio:
    cash_inr: float = INITIAL_INR
    positions: list[Position] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    tax_ledger: list[dict] = field(default_factory=list)
    peak_equity_inr: float = INITIAL_INR

    @classmethod
    def from_dict(cls, raw: dict) -> "Portfolio":
        return cls(
            cash_inr=float(raw.get("cash_inr", INITIAL_INR)),
            positions=[Position(**p) for p in raw.get("positions", [])],
            trades=list(raw.get("trades", [])), tax_ledger=list(raw.get("tax_ledger", [])),
            peak_equity_inr=float(raw.get("peak_equity_inr", INITIAL_INR)),
        )

    def to_dict(self) -> dict:
        return {"cash_inr": self.cash_inr, "positions": [asdict(p) for p in self.positions],
                "trades": self.trades, "tax_ledger": self.tax_ledger,
                "peak_equity_inr": self.peak_equity_inr}


@dataclass(frozen=True)
class Costs:
    fee_rate: float = 0.001
    slippage_bps: float = 10.0
    funding_rate_8h: float = 0.0001
    maintenance_margin_rate: float = 0.05
    max_position_fraction: float = 0.20
    max_daily_loss_fraction: float = 0.03

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
    if price <= 0 or bps < 0 or bps >= 10_000:
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


def open_position(portfolio: Portfolio, symbol: str, book: Book, side: Literal["long", "short"],
                  notional_inr: float, market_price: float, costs: Costs = Costs(),
                  hedge_ratio: float = 1.0, source: str = "market") -> Position:
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
    px = slipped_price(market_price, "buy" if side == "long" else "sell", costs.slippage_bps)
    maximum = max(0.0, portfolio.cash_inr) * costs.max_position_fraction
    if book == "futures_hedge":
        notional_inr *= hedge_ratio
    notional_inr = min(notional_inr, maximum)
    fee = notional_inr * costs.fee_rate
    margin = notional_inr if book != "spot" else 0.0
    debit = (notional_inr + fee) if book == "spot" else (margin + fee)
    if debit > portfolio.cash_inr:
        raise ValueError("Insufficient available cash/margin")
    portfolio.cash_inr -= debit
    pos = Position(symbol, book, notional_inr / px, px,
                   datetime.now(timezone.utc).isoformat(), margin)
    portfolio.positions.append(pos)
    portfolio.trades.append({"time": pos.opened_at, "symbol": symbol, "book": book,
        "side": side, "quantity": pos.quantity, "price_inr": px, "fee_inr": fee,
        "funding_inr": 0.0, "slippage_bps": costs.slippage_bps, "source": source,
        "hedge_ratio": hedge_ratio if book == "futures_hedge" else None})
    return pos


def charge_funding(portfolio: Portfolio, mark_prices: dict[str, float], periods: int = 1,
                   rate_per_8h: float = 0.0001) -> float:
    """Apply signed funding to short futures: positive rates credit shorts, negative debit them.

    Returns funding cost (positive means cash debit; negative means cash credit).
    """
    if periods < 0 or not math.isfinite(rate_per_8h):
        raise ValueError("Periods must be nonnegative and funding rate finite")
    for p in portfolio.positions:
        if p.book == "futures_hedge" and (p.symbol not in mark_prices or
                not math.isfinite(mark_prices[p.symbol]) or mark_prices[p.symbol] <= 0):
            raise ValueError("A valid mark price is required for every futures position")
    cost = sum(-p.quantity * mark_prices[p.symbol] * rate_per_8h * periods
               for p in portfolio.positions if p.book == "futures_hedge")
    portfolio.cash_inr -= cost
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

