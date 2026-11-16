"""Public market data adapters. Futures provenance is explicit."""
from __future__ import annotations
import requests


def spot_ohlc(symbol: str, days: int = 30) -> list[list[float]]:
    """CoinGecko public INR OHLC: [unix_ms, open, high, low, close] approximate candles."""
    ids = {"BTC/INR": "bitcoin", "ETH/INR": "ethereum"}
    if symbol not in ids:
        raise ValueError("Only BTC/INR and ETH/INR are supported")
    response = requests.get(f"https://api.coingecko.com/api/v3/coins/{ids[symbol]}/ohlc",
                            params={"vs_currency": "inr", "days": days}, timeout=15)
    response.raise_for_status()
    return response.json()


def futures_data_notice() -> dict:
    """Describe the real USD-margined feed and distinguish it from the INR paper book."""
    return {"configured": True, "source_type": "actual_exchange_futures", "exchanges": ["Binance USDⓈ-M", "Bybit linear perpetual"],
            "quote_currency": "USDT", "message": "Actual public Binance USDⓈ-M/Bybit linear futures adapters are configured in USDT. Network or regional restrictions may make the live feed unavailable; the paper hedge book remains simulated."}


def futures_snapshot(symbol: str) -> dict:
    """Public mark/funding only; fall back across venues, never call order endpoints."""
    pairs = {"BTC/INR": "BTCUSDT", "ETH/INR": "ETHUSDT"}
    if symbol not in pairs:
        raise ValueError("Only BTC/INR and ETH/INR equivalents are supported")
    errors = []
    try:
        response = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                                params={"symbol": pairs[symbol]}, timeout=15)
        response.raise_for_status()
        data = response.json()
        return {"underlying": symbol, "contract_symbol": data["symbol"],
                "mark_price_usdt": float(data["markPrice"]),
                "index_price_usdt": float(data["indexPrice"]),
                "funding_rate": float(data["lastFundingRate"]),
                "next_funding_time_ms": int(data["nextFundingTime"]),
                "source_type": "actual_exchange_futures", "exchange": "Binance USDⓈ-M",
                "quote_currency": "USDT", "retrieved_at_ms": int(data["time"])}
    except Exception as exc:
        errors.append(f"Binance: {exc}")
    try:
        response = requests.get("https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": pairs[symbol]}, timeout=15)
        response.raise_for_status()
        payload = response.json()
        if payload.get("retCode") != 0 or not payload.get("result", {}).get("list"):
            raise ValueError(payload.get("retMsg", "Bybit returned no ticker"))
        data = payload["result"]["list"][0]
        return {"underlying": symbol, "contract_symbol": data["symbol"],
                "mark_price_usdt": float(data["markPrice"]),
                "index_price_usdt": float(data["indexPrice"]),
                "funding_rate": float(data["fundingRate"]),
                "next_funding_time_ms": int(data["nextFundingTime"]),
                "source_type": "actual_exchange_futures", "exchange": "Bybit linear perpetual",
                "quote_currency": "USDT", "retrieved_at_ms": int(payload.get("time", 0)),
                "fallback_note": "Binance USDⓈ-M endpoint unavailable; fetched actual Bybit public ticker."}
    except Exception as exc:
        errors.append(f"Bybit: {exc}")
    raise RuntimeError("No public futures venue reachable. " + "; ".join(errors))
