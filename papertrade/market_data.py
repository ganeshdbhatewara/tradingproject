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
    return {"available": True, "source_type": "actual_exchange_futures", "exchange": "Binance USDⓈ-M",
            "quote_currency": "USDT", "message": "Actual Binance USDⓈ-M futures market data is quoted in USDT. It is not INR-denominated; the paper hedge book remains simulated."}


def futures_snapshot(symbol: str) -> dict:
    """Public mark/funding data only; no authentication or order endpoints are used."""
    pairs = {"BTC/INR": "BTCUSDT", "ETH/INR": "ETHUSDT"}
    if symbol not in pairs:
        raise ValueError("Only BTC/INR and ETH/INR equivalents are supported")
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
