import pytest

from papertrade import market_data


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_spot_prices_returns_valid_public_inr_marks(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return Response({"bitcoin": {"inr": 6_000_000}, "ethereum": {"inr": 170_000}})

    monkeypatch.setattr(market_data.requests, "get", fake_get)
    assert market_data.spot_prices() == {"BTC/INR": 6_000_000.0, "ETH/INR": 170_000.0}
    assert calls[0][0].endswith("/simple/price")
    assert calls[0][1]["vs_currencies"] == "inr"


@pytest.mark.parametrize("payload", [
    {},
    {"bitcoin": {"inr": 1}},
    {"bitcoin": {"inr": float("nan")}, "ethereum": {"inr": 2}},
    {"bitcoin": {"inr": 1}, "ethereum": {"inr": 0}},
])
def test_spot_prices_rejects_missing_or_invalid_marks(monkeypatch, payload):
    monkeypatch.setattr(market_data.requests, "get", lambda *args, **kwargs: Response(payload))
    with pytest.raises(ValueError):
        market_data.spot_prices()
