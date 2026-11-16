"""Small deterministic research helpers; no future-bar lookahead."""
from __future__ import annotations
import math
import pandas as pd


def classify_regime(closes: list[float], window: int = 20) -> str:
    if len(closes) < window + 1:
        return "insufficient_data"
    returns = pd.Series(closes).pct_change().dropna()
    drift = float(returns.tail(window).mean())
    vol = float(returns.tail(window).std(ddof=0))
    if vol > 0.03:
        return "volatile"
    if abs(drift) > 0.002:
        return "trending"
    return "ranging"


def walk_forward_returns(closes: list[float], train_fraction: float = 0.7) -> dict:
    if not 0.5 <= train_fraction < 1 or len(closes) < 10 or any(x <= 0 for x in closes):
        raise ValueError("Need positive prices and train_fraction in [0.5, 1).")
    split = int(len(closes) * train_fraction)
    returns = pd.Series(closes).pct_change().dropna()
    cut = max(1, split - 1)
    train = returns.iloc[:cut]
    test = returns.iloc[cut:]
    # Baseline buy/hold returns, reported separately; this is not strategy alpha.
    return {"train_return": float((1 + train).prod() - 1),
            "out_of_sample_return": float((1 + test).prod() - 1),
            "train_bars": int(len(train)), "out_of_sample_bars": int(len(test)),
            "method": "buy_and_hold_baseline"}


def detect_candle_patterns(rows: list[list[float]]) -> list[dict]:
    """Detect simple, explainable candle patterns from OHLC rows; not predictive guarantees."""
    results = []
    for i, row in enumerate(rows):
        if len(row) < 5:
            raise ValueError("Candles need timestamp, open, high, low, close")
        _, o, h, low, c = map(float, row[:5])
        if not all(math.isfinite(x) for x in (o, h, low, c)) or low <= 0 or h < max(o, c) or low > min(o, c):
            raise ValueError("Invalid OHLC candle")
        span = h - low
        body = abs(c - o)
        found = []
        if span > 0 and body / span <= 0.1:
            found.append({"pattern": "doji", "bias": "indecision"})
        lower_wick, upper_wick = min(o, c) - low, h - max(o, c)
        if span > 0 and lower_wick >= 2 * max(body, span * 0.01) and upper_wick <= span * 0.25:
            found.append({"pattern": "hammer", "bias": "potentially_bullish"})
        if i:
            _, po, ph, pl, pc = map(float, rows[i - 1][:5])
            if c > o and pc < po and o <= pc and c >= po:
                found.append({"pattern": "bullish_engulfing", "bias": "potentially_bullish"})
            elif c < o and pc > po and o >= pc and c <= po:
                found.append({"pattern": "bearish_engulfing", "bias": "potentially_bearish"})
        if found:
            results.append({"index": i, "timestamp": row[0], "patterns": found})
    return results


def strategy_signal(strategy: str, history: list[float]) -> int:
    """Return -1/0/+1 based on history ending before the execution bar."""
    if strategy not in {"Trend following", "Breakout", "Mean reversion", "Momentum"}:
        raise ValueError("Unknown strategy")
    if len(history) < 21:
        return 0
    last = history[-1]
    if strategy == "Trend following":
        fast, slow = sum(history[-10:]) / 10, sum(history[-20:]) / 20
        return 1 if fast > slow else -1 if fast < slow else 0
    if strategy == "Breakout":
        high, low = max(history[-21:-1]), min(history[-21:-1])
        return 1 if last > high else -1 if last < low else 0
    if strategy == "Mean reversion":
        sample = history[-20:]
        mean = sum(sample) / len(sample)
        sd = math.sqrt(sum((x - mean) ** 2 for x in sample) / len(sample))
        z = (last - mean) / sd if sd else 0
        return 1 if z < -1 else -1 if z > 1 else 0
    momentum = last / history[-13] - 1
    return 1 if momentum > 0.005 else -1 if momentum < -0.005 else 0


def backtest_ohlc(rows: list[list[float]], strategy: str, train_fraction: float = 0.7,
                  fee_rate: float = 0.001, slippage_bps: float = 10,
                  risk_fraction: float = 0.2) -> dict:
    """Unlevered single-position backtest; prior close signals fill at next open."""
    if len(rows) < 50 or not 0.5 <= train_fraction < 1:
        raise ValueError("At least 50 candles and train_fraction in [0.5, 1) are required")
    if not 0 <= fee_rate < 0.1 or not 0 <= slippage_bps < 10_000 or not 0 < risk_fraction <= 0.2:
        raise ValueError("Invalid costs or risk fraction")
    candles = [list(map(float, r[:5])) for r in rows]
    if any(len(r) != 5 or not all(math.isfinite(v) for v in r) or r[1] <= 0 or r[4] <= 0 or
           r[2] < max(r[1], r[4]) or r[3] > min(r[1], r[4]) or r[3] <= 0 or r[2] < r[3]
           for r in candles):
        raise ValueError("Invalid OHLC data")
    split = int(len(candles) * train_fraction)
    cash, equity_curve, trades = 20_000.0, [], []
    direction, entry, units, entry_fee = 0, None, 0.0, 0.0
    for i in range(21, len(candles)):
        signal = strategy_signal(strategy, [r[4] for r in candles[:i]])
        open_px = candles[i][1]
        target = signal
        if direction and target != direction:
            exit_px = open_px * (1 - slippage_bps / 10_000 if direction == 1 else 1 + slippage_bps / 10_000)
            exit_fee = units * exit_px * fee_rate
            pnl = units * (exit_px - entry) * direction - entry_fee - exit_fee
            cash += units * (exit_px - entry[0]) * direction - exit_fee
            trades.append({"entry_index": entry[1], "exit_index": i, "side": "long" if direction == 1 else "short",
                           "pnl_inr": pnl, "fees_inr": entry_fee + exit_fee})
            direction, entry, units, entry_fee = 0, None, 0.0, 0.0
        if not direction and target:
            fill = open_px * (1 + slippage_bps / 10_000 if target == 1 else 1 - slippage_bps / 10_000)
            notional = cash * risk_fraction
            units = notional / fill
            entry_fee = notional * fee_rate
            cash -= entry_fee
            direction, entry = target, (fill, i)
        mark = candles[i][4]
        unrealized = units * (mark - entry[0]) * direction if direction else 0
        equity_curve.append({"index": i, "equity_inr": cash + unrealized})
    if direction:
        px = candles[-1][4] * (1 - slippage_bps / 10_000 if direction == 1 else 1 + slippage_bps / 10_000)
        fee = units * px * fee_rate
        pnl = units * (px - entry[0]) * direction - entry_fee - fee
        cash += units * (px - entry[0]) * direction - fee
        trades.append({"entry_index": entry[1], "exit_index": len(candles) - 1,
                       "side": "long" if direction == 1 else "short", "pnl_inr": pnl,
                       "fees_inr": entry_fee + fee})
    train_equity = [p["equity_inr"] for p in equity_curve if p["index"] < split]
    test_equity = [p["equity_inr"] for p in equity_curve if p["index"] >= split]
    return {"strategy": strategy, "initial_capital_inr": 20_000,
            "final_equity_inr": cash, "net_return": cash / 20_000 - 1,
            "train_return": (train_equity[-1] / 20_000 - 1) if train_equity else 0,
            "out_of_sample_return": (test_equity[-1] / train_equity[-1] - 1) if test_equity and train_equity else 0,
            "trade_count": len(trades), "trades": trades, "equity_curve": equity_curve,
            "assumptions": "no leverage; signals use prior closes; fills use next open; fees/slippage applied; funding excluded"}
