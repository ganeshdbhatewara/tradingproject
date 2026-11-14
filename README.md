# Crypto AI Research and Paper Trading

Research, historical simulation, and paper portfolio controls only. There is no live order code, no exchange order credential support, and no real-money trading. Short and futures hedge positions are always simulated. A hedge can lose money and does not guarantee profit or remove risk.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
pytest -q
python worker.py
```

## Data provenance

- INR spot OHLC for BTC and ETH comes from the public CoinGecko endpoint.
- Actual USDⓈ-M futures mark/index/funding observations come from Binance public market data, in USDT. The app never represents these as INR futures or uses them as an order feed. API reference: [Binance USDⓈ-M market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
- Simulated INR hedge positions use the observed funding percentage only when the user clicks “Settle one simulated funding period”; funding cashflows are estimates against INR notional, not actual account payments.
- Pattern labels and regime detection are transparent OHLC heuristics. They are not guarantees or investment advice.

## Paper engine

`papertrade/engine.py` provides ₹20,000 initial cash; separate spot, short, and futures hedge books; entry/exit fees and slippage; signed funding; a per-order cap of 20% of available cash; a 3% daily-loss circuit breaker; a 20% peak-drawdown halt; simulated short liquidation; realized P&L and tax-ledger records. Tax due is intentionally left blank: ledger proceeds, cost basis, and fees are for recordkeeping and do not infer tax rules.

`papertrade/backtest.py` includes trend-following, breakout, mean-reversion, and momentum signals; a simple OHLC pattern recognizer; fees/slippage in a no-leverage simulator; and fixed-parameter train/out-of-sample reporting. It does not tune parameters on the test set. This is a research tool, not proof of strategy edge.

## Durable cloud portfolio state (Supabase)

Without cloud settings, the app uses atomic local JSON. That is useful locally but is **not durable** on Streamlit Community Cloud or GitHub-hosted runners. For durable state, create the table in Supabase SQL Editor:

```sql
create table public.paper_state (
  state_key text primary key,
  payload jsonb not null,
  revision bigint not null default 1,
  updated_at timestamptz not null default now()
);
alter table public.paper_state add column if not exists revision bigint not null default 1;
alter table public.paper_state enable row level security;
```

In Streamlit Community Cloud, add these server-side secrets (or set equivalent environment variables locally):

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SERVER_SIDE_SERVICE_ROLE_KEY"
PAPER_STATE_ID = "default"
```

The service role key bypasses RLS. Keep it only in server-side Streamlit secrets; never commit it or expose it in browser code. Use one private app deployment for this singleton paper portfolio. Revision checks reject stale concurrent writes and the app fails closed if cloud settings are partial or the configured database is unavailable; it does not silently fall back and overwrite local state. Configure backups and restrict access to deployment administrators.

## Streamlit deployment

Deploy this repository from Streamlit Community Cloud using `app.py` as the entry point. Configure Supabase secrets as above if durable state is required. No exchange API keys are used or needed.

## GitHub Actions research worker

The worker runs every six hours, on push/PR, and on manual dispatch. It reads public spot and futures market data, runs the test suite, and uploads `research-report.json` as a 14-day artifact. It never mutates paper portfolio state or places orders. No repository secrets are needed.

## Assumptions and limits

Fees, slippage, funding, and liquidation are simplified configurable estimates, not exchange contract specifications. Historical bars from public aggregators may be revised or rate-limited. Position book state is not proof of executable liquidity. Backtests exclude live latency, partial fills, funding history, taxes, and exchange margin schedules. Verify all output before using it for research decisions.
