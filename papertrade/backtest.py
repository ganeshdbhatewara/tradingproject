"""Small deterministic research helpers; no future-bar lookahead."""
from __future__ import annotations
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
