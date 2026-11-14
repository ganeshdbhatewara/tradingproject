"""Scheduled read-only market research. Does not place or simulate orders."""
import json
from datetime import datetime, timezone
from pathlib import Path
from papertrade.market_data import spot_ohlc, futures_data_notice
from papertrade.backtest import classify_regime, walk_forward_returns

report = {"generated_at": datetime.now(timezone.utc).isoformat(), "assets": {},
          "futures_data": futures_data_notice()}
for symbol in ("BTC/INR", "ETH/INR"):
    try:
        rows = spot_ohlc(symbol)
        closes = [float(row[4]) for row in rows]
        report["assets"][symbol] = {"source": "CoinGecko public spot", "regime": classify_regime(closes),
                                    "baseline": walk_forward_returns(closes)}
    except Exception as exc:
        report["assets"][symbol] = {"error": str(exc)}
Path("research-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
