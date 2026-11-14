"""Streamlit UI for research and simulated paper positions only."""
import os
import pandas as pd
import plotly.express as px
import streamlit as st
from papertrade.engine import (Costs, Portfolio, SYMBOLS, charge_funding, equity,
                               load_portfolio, open_position, save_portfolio, select_strategy)
from papertrade.market_data import futures_data_notice, spot_ohlc
from papertrade.backtest import classify_regime, walk_forward_returns

st.set_page_config(page_title="Crypto AI Research | Paper Trading", layout="wide")
st.title("Crypto AI Research & Paper Trading")
st.warning("SIMULATION ONLY · No live trading, exchange order credentials, or real-money orders. Futures and shorts are simulated.")
state_path = os.environ.get("PAPER_STATE_PATH", "state/portfolio.json")
portfolio = load_portfolio(state_path)
prices = {}
cols = st.columns(2)
for col, symbol in zip(cols, SYMBOLS):
    with col:
        st.subheader(symbol)
        try:
            candles = spot_ohlc(symbol)
            df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            prices[symbol] = float(df.close.iloc[-1])
            st.metric("Latest public spot (INR)", f"₹{prices[symbol]:,.2f}")
            st.plotly_chart(px.line(df, x="time", y="close", title="Public spot candles"), use_container_width=True)
            st.caption("Data: CoinGecko public spot endpoint; candle interval depends on requested range.")
            st.caption(f"Detected regime: {classify_regime(df.close.tolist())}")
            st.json(walk_forward_returns(df.close.tolist()))
        except Exception as exc:
            st.info(f"Market data unavailable: {exc}")

st.subheader("Strategy selector")
choice = st.selectbox("Mode", ["Auto", "Trend following", "Breakout", "Mean reversion", "Momentum"])
regime = st.selectbox("Observed regime", ["trending", "ranging", "volatile", "uncertain"])
trend = st.slider("Trend strength", -0.1, 0.1, 0.02)
volatility = st.slider("Volatility estimate", 0.0, 0.1, 0.02)
selection = select_strategy(regime, trend, volatility, choice)
st.write(f"**{selection['strategy']}** — {selection['reason']}")

st.subheader("Paper order")
with st.form("paper_order"):
    symbol = st.selectbox("Asset", SYMBOLS)
    book_label = st.selectbox("Position book", ["Spot long", "Short simulation", "Futures hedge simulation"])
    amount = st.number_input("Notional (INR)", min_value=100.0, max_value=4000.0, value=1000.0, step=100.0)
    ratio = st.slider("Futures hedge ratio", 0.0, 1.0, 0.5)
    submit = st.form_submit_button("Open simulated position")
if submit:
    if symbol not in prices:
        st.error("Price unavailable; order not submitted.")
    else:
        book = {"Spot long": "spot", "Short simulation": "short", "Futures hedge simulation": "futures_hedge"}[book_label]
        try:
            open_position(portfolio, symbol, book, "long" if book == "spot" else "short", amount,
                          prices[symbol], Costs(), hedge_ratio=ratio)
            save_portfolio(portfolio, state_path)
            st.success("Simulated position saved.")
        except ValueError as exc:
            st.error(str(exc))

st.subheader("Portfolio & audit trail")
if prices:
    st.metric("Marked equity", f"₹{equity(portfolio, prices):,.2f}")
else:
    st.metric("Cash", f"₹{portfolio.cash_inr:,.2f}")
st.write("Separate persistent books")
st.dataframe(pd.DataFrame([vars(p) for p in portfolio.positions]), use_container_width=True)
st.write("Trade journal")
st.dataframe(pd.DataFrame(portfolio.trades), use_container_width=True)
st.write("Tax ledger (realization events only; tax rules not inferred)")
st.dataframe(pd.DataFrame(portfolio.tax_ledger), use_container_width=True)
st.info(futures_data_notice()["message"])
