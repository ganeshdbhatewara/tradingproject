# Project Handoff

Checkpoint: 2026-11-15 (Asia/Kolkata)

## Saved branch and review

- Branch: `cloud-paper-trading-setup`
- Draft PR: https://github.com/ganeshdbhatewara/tradingproject/pull/1
- Last verified branch head before this handoff: `7aeeed089ab8f036f242036ba8c50803b96f9efc`
- PR is open, draft, and unmerged. Do not merge without review.

## Implemented

- Streamlit research dashboard for CoinGecko BTC/INR and ETH/INR spot OHLC.
- Heuristic doji, hammer, and engulfing patterns; regime classifier; explainable automatic/manual selection; trend, breakout, mean-reversion, and momentum signals.
- Historical single-position paper backtest with prior-close/next-open signal timing, fees/slippage, train and out-of-sample metrics.
- ₹20,000 paper portfolio; separate spot-long, simulated short, and simulated futures-hedge books; adjustable hedge ratio; entry/exit and signed funding accounting; simulated liquidation; 3% daily loss and 20% peak drawdown halt; position sizing cap.
- Trade journal, realized P&L and tax-ledger recordkeeping. Tax due is deliberately not inferred.
- Atomic local JSON storage plus optional Supabase REST storage with optimistic revision checks.
- Scheduled GitHub Actions research, tests, and 14-day research artifact upload.
- No live exchange orders, exchange order credentials, or real-money trading.

## Last validation

- GitHub Actions run `43626045889` completed successfully: 19 tests passed, research worker succeeded, and the report artifact uploaded.
- Futures API calls from the GitHub runner were geo/region blocked (Binance HTTP 451, Bybit HTTP 403). Worker output explicitly marks the futures feed unavailable; it does not substitute synthetic data.
- Local `git` and Python executables are unavailable in this workspace. Branch changes have been committed through the connected GitHub integration.

## Configuration and known limits

- Durable cloud state requires creating the `public.paper_state` Supabase table from the SQL in README and setting `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and optionally `PAPER_STATE_ID` as server-side Streamlit secrets. This has not been exercised against a live Supabase project. Local JSON is ephemeral on cloud hosts.
- Actual futures adapters return USDⓈ/USDT data, not INR futures. Availability depends on hosting region. The paper hedge remains simulated.
- Tax ledger captures proceeds, basis, fees, funding, and realized P&L, but does not calculate tax owed.
- Pattern/regime methods, funding application, fee/slippage assumptions, and margin/liquidation are educational approximations; historical backtests do not include historical funding, exchange margin schedules, partial fills, or latency.
- Streamlit has deployment instructions but is not deployed.

## Next review items

1. Review the current draft PR and run CI on its latest head.
2. Configure Supabase in a private Streamlit deployment and verify create/read/update and stale-write rejection against the real project.
3. Check public futures endpoint availability from the intended deployment region; keep explicit unavailable provenance when blocked.
4. Review and refine risk, liquidation, backtest, and tax-ledger assumptions before considering production research use.

