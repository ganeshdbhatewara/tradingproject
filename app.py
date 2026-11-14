"""Research dashboard and simulated portfolio controls. No live order execution."""
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from papertrade.engine import (Costs, SYMBOLS, charge_funding, check_liquidations,
    close_position, equity, open_position, risk_status, select_strategy, RiskLimitError)
from papertrade.market_data import futures_data_notice, futures_snapshot, spot_ohlc
from papertrade.persistence import load_state, save_state
from papertrade.backtest import (backtest_ohlc, classify_regime, detect_candle_patterns,
    strategy_signal, walk_forward_returns)

st.set_page_config(page_title="Crypto AI Research | Paper Trading", layout="wide")
st.title("Crypto AI Research & Paper Trading")
st.warning("SIMULATION ONLY · No live orders, exchange credentials, or real-money trading. Shorts and hedge positions are simulated.")

@st.cache_data(ttl=60, show_spinner=False)
def cached_spot(symbol):
    return spot_ohlc(symbol)

@st.cache_data(ttl=60, show_spinner=False)
def cached_futures(symbol):
    return futures_snapshot(symbol)
state_path = os.environ.get("PAPER_STATE_PATH", "state/portfolio.json")
try:
    for secret_name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "PAPER_STATE_ID"):
        if secret_name in st.secrets:
            os.environ[secret_name] = str(st.secrets[secret_name])
except Exception:
    pass
try:
    portfolio, persistence_label = load_state(state_path)
except Exception as exc:
    st.error(f"Persistent state could not be loaded; trading is disabled to avoid overwriting state. {exc}")
    st.stop()

prices, spot_rows, futures = {}, {}, {}
cols = st.columns(2)
for col, symbol in zip(cols, SYMBOLS):
    with col:
        st.subheader(symbol)
        try:
            rows = cached_spot(symbol)
            spot_rows[symbol] = rows
            df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            prices[symbol] = float(df.close.iloc[-1])
            st.metric("Public spot (INR)", f"₹{prices[symbol]:,.2f}")
            fig = go.Figure(data=[go.Candlestick(x=df.time, open=df.open, high=df.high,
                low=df.low, close=df.close, name="CoinGecko spot")])
            fig.update_layout(title="Public spot OHLC", xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("CoinGecko public spot data · INR · candle interval depends on requested range.")
            regime = classify_regime(df.close.tolist())
            st.caption(f"Heuristic market regime: {regime}")
            patterns = detect_candle_patterns(rows)
            st.write("Recent candle pattern candidates (heuristic, not predictive guarantees)")
            st.dataframe(pd.DataFrame(patterns[-10:]), use_container_width=True)
            try:
                futures[symbol] = cached_futures(symbol)
                fut = futures[symbol]
                st.caption(f"Actual futures · {fut['exchange']} · {fut['contract_symbol']} · quote: USDT")
                st.metric("Futures mark (USDT)", f"{fut['mark_price_usdt']:,.2f}",
                          f"Funding {fut['funding_rate']:.5%}")
            except Exception as exc:
                st.info(f"Actual futures feed unavailable: {exc}. Any hedge ledger remains simulated.")
            st.json(walk_forward_returns(df.close.tolist()))
        except Exception as exc:
            st.info(f"Spot data unavailable: {exc}")

st.subheader("Strategy research")
strategy_names = ["Trend following", "Breakout", "Mean reversion", "Momentum"]
mode = st.selectbox("Strategy mode", ["Auto", *strategy_names])
default_regime = classify_regime(spot_rows["BTC/INR"] and [r[4] for r in spot_rows["BTC/INR"]]) if "BTC/INR" in spot_rows else "uncertain"
regime_override = st.selectbox("Regime", ["Auto", "trending", "ranging", "volatile", "uncertain"])
regime = default_regime if regime_override == "Auto" else regime_override
closes = [float(r[4]) for r in spot_rows.get("BTC/INR", [])]
trend = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else 0.0
volatility = float(pd.Series(closes).pct_change().tail(20).std(ddof=0)) if len(closes) >= 21 else 0.0
selection = select_strategy(regime, trend, volatility, mode)
st.write(f"**Auto/selected strategy: {selection['strategy']}** — {selection['reason']}")
for name in strategy_names:
    st.caption(f"{name}: {strategy_signal(name, closes) if len(closes) >= 21 else 0:+d} signal (−1 short, 0 flat, +1 long)")
backtest_strategy = st.selectbox("Backtest strategy", strategy_names, index=strategy_names.index(selection["strategy"]))
backtest_symbol = st.selectbox("Backtest asset", SYMBOLS, key="backtest_asset")
if backtest_symbol in spot_rows and len(spot_rows[backtest_symbol]) >= 50:
    try:
        result = backtest_ohlc(spot_rows[backtest_symbol], backtest_strategy)
        st.write({k: result[k] for k in ("initial_capital_inr", "final_equity_inr", "net_return",
                                         "train_return", "out_of_sample_return", "trade_count", "assumptions")})
        st.line_chart(pd.DataFrame(result["equity_curve"]).set_index("index")["equity_inr"])
    except Exception as exc:
        st.error(f"Backtest could not run: {exc}")

costs = Costs()
status = None
if prices and all(p.symbol in prices for p in portfolio.positions):
    try:
        liquidations = check_liquidations(portfolio, prices, costs)
        if liquidations:
            save_state(portfolio, state_path)
            st.error(f"{len(liquidations)} simulated position(s) were closed at the liquidation estimate.")
        status = risk_status(portfolio, prices, costs)
        save_state(portfolio, state_path)
    except Exception as exc:
        st.error(f"Risk valuation unavailable: {exc}. New positions are disabled.")
        status = {"halted": True}
else:
    st.warning("Risk checks require current INR spot marks for every open position; entries and settlements are disabled while marks are missing.")
    status = {"halted": True}

st.subheader("Paper trading controls")
with st.form("paper_order"):
    symbol = st.selectbox("Asset", SYMBOLS)
    book_label = st.selectbox("Position book", ["Spot long", "Short simulation", "Futures hedge simulation"])
    amount = st.number_input("Requested notional (INR)", min_value=100.0, max_value=4000.0, value=1000.0, step=100.0)
    ratio = st.slider("Futures hedge ratio", 0.0, 1.0, 0.5)
    submit = st.form_submit_button("Open simulated position", disabled=bool(status and status.get("halted")) or symbol not in prices)
if submit:
    book = {"Spot long": "spot", "Short simulation": "short", "Futures hedge simulation": "futures_hedge"}[book_label]
    try:
        open_position(portfolio, symbol, book, "long" if book == "spot" else "short", amount,
                      prices[symbol], costs, hedge_ratio=ratio, mark_prices=prices)
        st.success(f"Saved to {save_state(portfolio, state_path)}.")
    except (ValueError, RiskLimitError, RuntimeError) as exc:
        st.error(str(exc))

st.write("Automatic signal to simulated position")
auto_symbol = st.selectbox("Signal asset", SYMBOLS, key="auto_signal_asset")
auto_closes = [float(r[4]) for r in spot_rows.get(auto_symbol, [])]
auto_signal = strategy_signal(selection["strategy"], auto_closes) if len(auto_closes) >= 21 else 0
signal_label = {1: "bullish · open a simulated spot long", -1: "bearish · open a simulated short", 0: "flat · no position"}[auto_signal]
st.caption(f"{selection['strategy']} signal: {signal_label}")
auto_amount = st.number_input("Automatic signal notional (INR)", min_value=100.0,
                              max_value=4000.0, value=1000.0, step=100.0, key="auto_amount")
if st.button("Execute signal in paper portfolio", disabled=auto_signal == 0 or auto_symbol not in prices or bool(status and status.get("halted"))):
    book = "spot" if auto_signal > 0 else "short"
    try:
        open_position(portfolio, auto_symbol, book, "long" if auto_signal > 0 else "short",
            auto_amount, prices[auto_symbol], costs, source=f"strategy:{selection['strategy']}",
            mark_prices=prices)
        st.success(f"Signal opened as a simulated {book} position. {save_state(portfolio, state_path)}.")
    except (ValueError, RiskLimitError, RuntimeError) as exc:
        st.error(str(exc))

if portfolio.positions:
    st.write("Close a paper position")
    choices = {f"{p.symbol} · {p.book} · {p.position_id[:8]}": p for p in portfolio.positions}
    label = st.selectbox("Position", list(choices))
    if st.button("Close selected position", disabled=choices[label].symbol not in prices):
        try:
            p = choices[label]
            event = close_position(portfolio, p.position_id, prices[p.symbol], costs)
            save_state(portfolio, state_path)
            st.success(f"Closed. Realized simulated P&L: ₹{event['realized_pnl_inr']:,.2f}")
        except Exception as exc:
            st.error(str(exc))

futures_positions = [p for p in portfolio.positions if p.book == "futures_hedge"]
if futures_positions:
    funding_event_id = "|".join(sorted(
        f"{futures[p.symbol]['exchange']}:{p.symbol}:{futures[p.symbol]['next_funding_time_ms']}"
        for p in futures_positions if p.symbol in futures))
    funding_ready = all(p.symbol in futures for p in futures_positions)
    if st.button("Settle one simulated funding period", disabled=not funding_ready or funding_event_id in portfolio.funding_events):
        try:
            rates = {p.symbol: futures[p.symbol]["funding_rate"] for p in futures_positions}
            cost = charge_funding(portfolio, prices, rate_per_8h=rates, event_id=funding_event_id)
            save_state(portfolio, state_path)
            st.success(f"Funding cost ₹{cost:,.2f} (negative means received). Latest observed exchange rates were applied to simulated INR notional.")
        except Exception as exc:
            st.error(str(exc))

st.subheader("Portfolio, risk, and audit trail")
st.caption(f"State backend: {persistence_label}")
if prices and all(p.symbol in prices for p in portfolio.positions):
    st.metric("Marked equity", f"₹{equity(portfolio, prices):,.2f}")
    st.metric("Daily loss", f"{status['daily_loss_fraction']:.2%}",
              help="New entries halt at 3% daily loss or 20% peak drawdown.")
else:
    st.metric("Cash", f"₹{portfolio.cash_inr:,.2f}")
st.write("Separate spot, short, and futures hedge books")
st.dataframe(pd.DataFrame([vars(p) for p in portfolio.positions]), use_container_width=True)
st.write("Trade journal")
st.dataframe(pd.DataFrame(portfolio.trades), use_container_width=True)
st.write("Realized tax ledger (records proceeds, basis, fees, and realized P&L; does not calculate tax owed)")
st.dataframe(pd.DataFrame(portfolio.tax_ledger), use_container_width=True)
st.info(futures_data_notice()["message"])
