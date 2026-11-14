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
    """This MVP has no INR-denominated futures feed. Never substitute simulated data silently."""
    return {"available": False, "source_type": "unavailable",
            "message": "No verified INR futures feed is configured. Internal hedge book is simulated; do not present it as exchange futures data."}
