# Crypto AI Research and Paper Trading

This project is a research dashboard and **simulation only** paper ledger. It never connects to exchange order endpoints and has no live order implementation. Short and futures positions are simulated. A hedge can lose money and does not guarantee profit or eliminate losses.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Run checks with `pytest -q`. `python worker.py` retrieves public spot data and writes a research report.

## Architecture and current limits

- `papertrade/market_data.py`: CoinGecko public BTC/INR and ETH/INR OHLC. This is spot market data, not an exchange execution feed.
- `papertrade/engine.py`: INR 20,000 initial cash; separate spot, short simulation, and futures hedge books; JSON persistence; position sizing, fees, slippage, funding, and an approximate isolated margin liquidation estimate.
- `papertrade/backtest.py`: regime heuristic and a buy-and-hold train/test baseline. This is not strategy performance or evidence of an edge.
- `app.py`: Streamlit dashboard, transparent manual/automatic strategy selection, journal and portfolio display.
- `.github/workflows/research.yml`: scheduled read-only market research and test run.

The futures book currently has **no verified INR futures data source**. Its hedge entries and funding are simulated assumptions and are labeled as such. Margin/liquidation is a simplified educational model, not an exchange contract specification. The present regime and strategy selector are transparent heuristics; chart patterns, four executable strategies, trade exits, realized P&L/tax calculations, daily loss circuit breaker, and durable multi-replica cloud storage are not yet implemented. JSON on Streamlit/GitHub-hosted ephemeral disks is not durable cloud persistence. Do not use this as financial or tax advice.

## Streamlit deployment

Deploy this repository from Streamlit Community Cloud using `app.py` as the entry point. No exchange API keys or secrets are required. The default `state/portfolio.json` is local to the app instance and may reset on restart/redeploy; do not treat it as cloud-persistent storage. Before multi-user or durable deployment, add an authenticated database backend with transactional writes, backup/restore, concurrency control, and per-user authorization.

## GitHub Actions

The scheduled worker runs every six hours and on manual dispatch. It uses public unauthenticated market data and does not need repository secrets. Reports generated in the runner are temporary unless a separate artifact or storage retention policy is configured. It does not mutate portfolio state or trade.

## Risk and data integrity

The initial virtual balance is ₹20,000; an order is capped at 20% of available cash. Fees and slippage reduce cash and simulated futures funding reduces cash. These assumptions are configurable in code. There is no leverage parameter, but the current margin model and unfinished risk controls are not suitable for actual trading. Missing spot prices block order submission. Never present synthetic or unavailable futures observations as actual data.
