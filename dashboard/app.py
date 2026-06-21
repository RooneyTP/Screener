# dashboard/app.py — Live Monitoring Dashboard (SKILL.md §⑥)
# Run: streamlit run dashboard/app.py
# v3.0: Full tearsheet + stock drill-down + walk-forward + AI + PnL + alerts
#
# ── STRATEGY ASSUMPTIONS ─────────────────────────────────────────
# Commission:      0.04% per side (crypto) / 0.15%-0.25% (equities)
# Slippage model:  0.10% per side (static)
# Position sizing: Fixed fractional, 1% risk per trade
# Leverage:        1× (no margin)
# ─────────────────────────────────────────────────────────────────

import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import os
import sys
import glob as _glob
from datetime import datetime, timedelta
import plotly.graph_objects as go
import plotly.express as px

# ── Load .env file (SKILL.md: never hardcode API keys) ────────────────
from dotenv import load_dotenv
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_ENV_PATH)

# ── Resolve project root (c:/Screener) from this file's location ──────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ── Page Config ───────────────────────────────────────────────────────
st.set_page_config(
    page_title="Quant Trader — Live Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Inject sidebar YAML config display ────────────────────────────────
import yaml
_config_path = os.path.join(_PROJECT_ROOT, "config", "settings.yaml")
_cfg: dict = {}
try:
    with open(_config_path, "r") as _cf:
        _cfg = yaml.safe_load(_cf) or {}
except Exception as e:
    st.sidebar.warning(f"Cannot read config: {e}")

st.sidebar.header("⚙️ Strategy Config")
_strat = _cfg.get("strategy", {})
st.sidebar.caption(
    f"Mode: **{_strat.get('mode','?')}**  |  Asset: **{_strat.get('asset_class','?')}**  "
    f"Exchange: **{_strat.get('exchange','?')}**"
)
_risk = _cfg.get("risk", {})
_ks = _risk.get("kill_switch", {})
st.sidebar.caption(
    f"Risk/Trade: **{_risk.get('per_trade_risk_pct',0.01)*100:.0f}%**  |  "
    f"Daily Halt: **{_ks.get('max_daily_loss_pct',0.05)*100:.0f}%**  |  "
    f"Peak DD Halt: **{_ks.get('max_drawdown_pct',0.20)*100:.0f}%**"
)
st.sidebar.divider()

# ── CSS tweaks ────────────────────────────────────────────────────────
st.markdown("""
<style>
    .metric-positive { color: #00ff88; }
    .metric-negative { color: #ff4444; }
    .kill-active { font-size: 1.4em; color: #00ff88; font-weight: bold; }
    .kill-triggered { font-size: 1.4em; color: #ff4444; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# =====================================================================
#  HELPERS
# =====================================================================

@st.cache_data(ttl=120)
def load_screener_data() -> pd.DataFrame:
    """Load screener data: SQLite first (faster), CSV as fallback."""
    # ── Strategy 1: SQLite (faster, created by telegram_bot.py) ─────────
    sqlite_path = os.path.join(_PROJECT_ROOT, "screener_results.db")
    if os.path.exists(sqlite_path):
        try:
            import sqlite3
            with sqlite3.connect(sqlite_path) as conn:
                df = pd.read_sql("SELECT * FROM screener_results ORDER BY skor DESC", conn)
            if not df.empty:
                return df
        except Exception:
            pass
    
    # ── Strategy 2: CSV files (full columns including AI) ──────────────
    # ── Strategy 1: CSV files (full columns including AI) ──────────────
    frames = []
    for pattern in [
        os.path.join(_PROJECT_ROOT, "screener_ihsg_*.csv"),
        os.path.join(_PROJECT_ROOT, "Data Screener", "screener_ihsg_*.csv"),
    ]:
        for p in sorted(_glob.glob(pattern)):
            try:
                fname = os.path.basename(p)
                ds = fname.replace("screener_ihsg_", "").replace(".csv", "")
                if len(ds) == 8 and ds.isdigit():
                    hdf = pd.read_csv(p)
                    if not hdf.empty:
                        hdf["Tanggal"] = pd.to_datetime(f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}")
                        frames.append(hdf)
            except Exception:
                continue
    if frames:
        df = pd.concat(frames, ignore_index=True)
        return df

    # ── Strategy 2: Parquet fallback (may lack AI columns) ────────────
    parquet_path = os.path.join(_PROJECT_ROOT, "data_lake", "histori_ihsg.parquet")
    if os.path.exists(parquet_path):
        df = pd.read_parquet(parquet_path)
        if "Tanggal" in df.columns:
            df["Tanggal"] = pd.to_datetime(df["Tanggal"])
        return df
    return pd.DataFrame()


@st.cache_data(ttl=30)
def load_portfolio(db_path: str | None = None) -> dict:
    """Load virtual portfolio from SQLite."""
    if db_path is None:
        db_path = os.path.join(_PROJECT_ROOT, "portofolio_virtual.db")
    if not os.path.exists(db_path):
        return {"equity": 0, "cash": 0, "positions": [], "history": [], "initial": 10_000_000}
    conn = sqlite3.connect(db_path)
    try:
        cash_row = conn.execute("SELECT saldo_cash FROM akun").fetchone()
        cash = cash_row[0] if cash_row else 0
    except Exception:
        cash = 0
    try:
        pos = pd.read_sql("SELECT * FROM posisi", conn).to_dict("records")
    except Exception:
        pos = []
    try:
        hist = pd.read_sql(
            "SELECT * FROM histori_trade ORDER BY tanggal DESC LIMIT 200", conn
        ).to_dict("records")
    except Exception:
        hist = []
    conn.close()
    equity = cash + sum(p.get("harga_beli", 0) * p.get("shares", 0) for p in pos)
    return {"equity": equity, "cash": cash, "positions": pos, "history": hist, "initial": 10_000_000}


def calc_today_pnl(portfolio: dict) -> dict:
    """Estimate today's PnL by comparing entry prices to current market prices."""
    positions = portfolio.get("positions", [])
    if not positions:
        return {"pnl_idr": 0, "pnl_pct": 0.0, "winners": 0, "losers": 0, "details": []}

    total_pnl = 0.0
    winners = 0
    losers = 0
    details = []
    try:
        import yfinance as yf
        tickers = [f"{p.get('ticker','')}.JK" for p in positions if p.get("ticker")]
        if tickers:
            raw = yf.download(tickers, period="1d", progress=False, threads=False)
            for p in positions:
                tkr = p.get("ticker", "")
                tkr_full = f"{tkr}.JK"
                entry = p.get("harga_beli", 0)
                shares = p.get("shares", 0)
                try:
                    if isinstance(raw.columns, pd.MultiIndex):
                        current = float(raw["Close"][tkr_full].dropna().iloc[-1])
                    else:
                        current = float(raw["Close"].dropna().iloc[-1]) if len(tickers) == 1 else entry
                except Exception:
                    current = entry
                pnl = (current - entry) * shares if entry > 0 else 0
                total_pnl += pnl
                if pnl > 0:
                    winners += 1
                elif pnl < 0:
                    losers += 1
                details.append({
                    "ticker": tkr,
                    "entry": entry,
                    "current": current,
                    "shares": shares,
                    "pnl": int(pnl),
                    "pnl_pct": round((current - entry) / entry * 100, 2) if entry > 0 else 0,
                })
    except Exception:
        pass

    equity = portfolio.get("equity", 10_000_000)
    pnl_pct = (total_pnl / equity * 100) if equity > 0 else 0.0
    return {"pnl_idr": int(total_pnl), "pnl_pct": round(pnl_pct, 2), "winners": winners, "losers": losers, "details": details}


@st.cache_data(ttl=120)
def run_backtest_on_history() -> dict:
    """Run event-driven backtest on all historical screener data."""
    df = load_screener_data()
    if df.empty:
        return {"error": "No screener data available"}
    try:
        from backtest import backtest, compute_tearsheet
        # Filter to buy signals only
        signals = df[df["Sinyal"].isin(["BUY", "STRONG_BUY", "ULTRA_BUY"])].copy()
        if len(signals) < 10:
            return {"error": f"Only {len(signals)} signals — need ≥10 for statistical validity"}
        # Run backtest with empty prices_df → deterministic exit model
        win_rate, sharpe = backtest(signals, pd.DataFrame())

        # Compute tearsheet from individual trade returns
        trade_returns = []
        for _, row in signals.iterrows():
            entry_raw = float(row.get("Harga", 0) or 0)
            tp_raw = float(row.get("Target_1", 0) or 0)
            sl_raw = float(row.get("Stop_Loss", 0) or 0)
            rrr = float(row.get("RRR", 0) or 0)
            conf = float(row.get("Confidence%", 0) or 0)
            skor = float(row.get("Skor", 0) or 0)
            if entry_raw <= 0 or tp_raw <= 0 or sl_raw <= 0:
                continue
            quality = (conf / 100) * 0.6 + max(0, min(1, skor / 15)) * 0.4
            if rrr >= 2.0 and quality >= 0.55:
                net_ret = (tp_raw / entry_raw - 1) * (1 - 0.001 - 0.0025)
            elif rrr < 1.5 or quality < 0.35:
                net_ret = (sl_raw / entry_raw - 1) * (1 - 0.001 - 0.0025)
            else:
                net_ret = -0.005  # breakeven ± costs
            trade_returns.append(min(net_ret, 0.50))  # cap at +50%

        tearsheet = compute_tearsheet(trade_returns) if trade_returns else {}
        # Build equity curve from cumulative returns
        if trade_returns:
            cumulative = list(np.cumprod([1 + r for r in trade_returns]))
            peak = list(np.maximum.accumulate(cumulative))
            drawdowns = [(p - c) / p * 100 if p > 0 else 0 for c, p in zip(cumulative, peak)]
        else:
            cumulative, peak, drawdowns = [], [], []

        return {
            "win_rate": win_rate * 100,
            "sharpe": sharpe,
            "total_trades": len(trade_returns),
            "tearsheet": tearsheet,
            "equity_curve": cumulative,
            "drawdown_curve": drawdowns,
        }
    except ImportError:
        return {"error": "backtest.py module not found"}
    except Exception as e:
        return {"error": str(e)}


@st.cache_data(ttl=120)
def run_walk_forward() -> dict:
    """Run walk-forward optimization on historical screener data."""
    df = load_screener_data()
    if df.empty:
        return {"error": "No data"}
    try:
        from backtest import walk_forward_optimize
        signals_by_date = {}
        for d, grp in df.groupby("Tanggal"):
            ds = str(d.date()) if hasattr(d, "date") else str(d)[:10]
            signals_by_date[ds] = grp.copy()
        if len(signals_by_date) < 6:
            return {"error": f"Need ≥6 days, have {len(signals_by_date)}"}
        return walk_forward_optimize(signals_by_date)
    except ImportError:
        return {"error": "backtest.py module not found"}
    except Exception as e:
        return {"error": str(e)}


# =====================================================================
#  LAYOUT
# =====================================================================

st.title("📊 FullStack Quant Trader — Live Monitor")
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}  |  Auto-refresh: 60s")

# Tabs for organization
tab_account, tab_backtest, tab_drilldown, tab_wf, tab_alerts, tab_scalp = st.tabs([
    "💰 Account & Positions",
    "📈 Backtest Tearsheet",
    "🔍 Stock Drill-Down",
    "🧪 Walk-Forward + AI",
    "📡 Alerts & Notifications",
    "⚡ Scalping",
])

# =====================================================================
#  TAB 1: ACCOUNT & POSITIONS
# =====================================================================
with tab_account:
    portfolio = load_portfolio()
    today_pnl = calc_today_pnl(portfolio)

    # Row 1: Account Summary
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("💰 Total Equity", f"Rp {portfolio['equity']:,.0f}")
    with col2:
        st.metric("💵 Cash", f"Rp {portfolio['cash']:,.0f}")
    with col3:
        pnl_color = "normal"
        st.metric(
            "📊 Today's PnL",
            f"Rp {today_pnl['pnl_idr']:+,.0f}",
            delta=f"{today_pnl['pnl_pct']:+.2f}%",
        )
    with col4:
        margin = ((portfolio["equity"] - portfolio["cash"]) / max(1, portfolio["equity"]) * 100)
        st.metric("📐 Margin Used", f"{margin:.1f}%")
    with col5:
        initial = portfolio.get("initial", 10_000_000)
        total_return = (portfolio["equity"] - initial) / initial * 100 if initial > 0 else 0
        st.metric("📈 Total Return", f"{total_return:+.2f}%")

    # Row 2: Kill Switch Status
    col_ks1, col_ks2 = st.columns([1, 3])
    with col_ks1:
        st.subheader("🛡️ Kill Switch")
        try:
            from risk.kill_switch import KillSwitch
            # Check against current equity
            ks = KillSwitch()
            peak = max(initial, portfolio["equity"])
            allowed, reason = ks.check(portfolio["equity"], peak, initial)
            if allowed:
                st.markdown('<p class="kill-active">🟢 ACTIVE</p>', unsafe_allow_html=True)
            else:
                st.markdown(f'<p class="kill-triggered">🔴 TRIGGERED</p>', unsafe_allow_html=True)
                st.error(reason)
        except ImportError:
            st.warning("Module not found")

    with col_ks2:
        st.subheader("📋 Risk Limits")
        st.caption(
            f"Daily Halt: {_ks.get('max_daily_loss_pct',0.05)*100:.0f}%  |  "
            f"Weekly: {_ks.get('max_weekly_loss_pct',0.08)*100:.0f}%  |  "
            f"Monthly: {_ks.get('max_monthly_loss_pct',0.15)*100:.0f}%  |  "
            f"Peak DD Kill: {_ks.get('max_drawdown_pct',0.20)*100:.0f}%"
        )

    st.divider()

    # Row 3: Open Positions
    st.subheader("📋 Open Positions")
    positions = portfolio["positions"]
    if positions:
        pos_data = []
        for p in positions:
            tkr = p.get("ticker", "?")
            pnl_detail = next((d for d in today_pnl.get("details", []) if d["ticker"] == tkr), None)
            pos_data.append({
                "Ticker": tkr,
                "Side": "BUY",
                "Shares": p.get("shares", 0),
                "Entry": f"Rp {p.get('harga_beli', 0):,}",
                "Current": f"Rp {pnl_detail['current']:,}" if pnl_detail else "—",
                "SL": f"Rp {p.get('sl', 0):,}",
                "TP": f"Rp {p.get('tp', 0):,}",
                "PnL (IDR)": f"Rp {pnl_detail['pnl']:+,}" if pnl_detail else "—",
                "PnL %": f"{pnl_detail['pnl_pct']:+.2f}%" if pnl_detail else "—",
            })
        st.dataframe(pd.DataFrame(pos_data), use_container_width=True, hide_index=True)

        # Mini equity curve for positions
        if today_pnl["details"]:
            pnl_detail_df = pd.DataFrame(today_pnl["details"])
            if not pnl_detail_df.empty:
                fig_pnl = px.bar(
                    pnl_detail_df, x="ticker", y="pnl",
                    title="Today's PnL by Position",
                    color="pnl",
                    color_continuous_scale=["red", "lightgray", "green"],
                    color_continuous_midpoint=0,
                )
                st.plotly_chart(fig_pnl, use_container_width=True)
    else:
        st.info("No open positions — portfolio is flat.")

    st.divider()

    # Row 4: Trade History
    st.subheader("📜 Recent Trade History")
    history = portfolio["history"]
    if history:
        hist_df = pd.DataFrame(history)
        if "tanggal" in hist_df.columns:
            hist_df["tanggal"] = pd.to_datetime(hist_df["tanggal"])
        # PnL summary for history
        if "pnl_idr" in hist_df.columns:
            total_hist_pnl = hist_df["pnl_idr"].sum()
            win_trades = hist_df[hist_df["pnl_idr"] > 0]
            loss_trades = hist_df[hist_df["pnl_idr"] < 0]
            col_h1, col_h2, col_h3 = st.columns(3)
            with col_h1:
                st.metric("Total Trades", len(hist_df))
            with col_h2:
                st.metric("Win Rate", f"{len(win_trades)/max(1,len(hist_df))*100:.1f}%")
            with col_h3:
                st.metric("Cumulative PnL", f"Rp {total_hist_pnl:+,.0f}")
        st.dataframe(hist_df.tail(30), use_container_width=True, hide_index=True)
    else:
        st.info("No trade history yet.")


# =====================================================================
#  TAB 2: BACKTEST TEARSHEET
# =====================================================================
with tab_backtest:
    st.subheader("📈 Backtest Performance Tearsheet (SKILL.md §③)")

    bt_result = run_backtest_on_history()

    if "error" in bt_result:
        st.warning(f"Backtest not available: {bt_result['error']}")
    else:
        # Metrics in columns
        col_b1, col_b2, col_b3, col_b4, col_b5, col_b6 = st.columns(6)
        ts = bt_result.get("tearsheet", {})
        with col_b1:
            st.metric("Total Trades", bt_result.get("total_trades", 0))
        with col_b2:
            st.metric("Win Rate", f"{bt_result.get('win_rate', 0):.1f}%")
        with col_b3:
            st.metric("Sharpe Ratio", f"{bt_result.get('sharpe', 0):.2f}")
        with col_b4:
            st.metric("Sortino Ratio", f"{ts.get('sortino_ratio', 0):.2f}")
        with col_b5:
            st.metric("Profit Factor", f"{ts.get('profit_factor', 0):.2f}")
        with col_b6:
            st.metric("Max Drawdown", f"{ts.get('max_drawdown_pct', 0):.1f}%")

        col_b7, col_b8, col_b9, col_b10 = st.columns(4)
        with col_b7:
            st.metric("Avg Win", f"{ts.get('avg_win_pct', 0):.3f}%")
        with col_b8:
            st.metric("Avg Loss", f"{ts.get('avg_loss_pct', 0):.3f}%")
        with col_b9:
            st.metric("Expectancy (R)", f"{ts.get('expectancy_r', 0):.4f}")
        with col_b10:
            st.metric("Avg Win/Loss", f"{ts.get('avg_win_pct', 0)/max(0.001, abs(ts.get('avg_loss_pct', 0.001))):.1f}x")

        st.divider()

        # Equity Curve + Drawdown Chart
        equity = bt_result.get("equity_curve", [])
        drawdown = bt_result.get("drawdown_curve", [])
        if equity:
            col_eq, col_dd = st.columns(2)
            with col_eq:
                fig_eq = go.Figure()
                fig_eq.add_trace(go.Scatter(
                    y=equity, mode="lines", name="Equity Curve",
                    line=dict(color="#00ff88", width=2),
                    fill="tozeroy", fillcolor="rgba(0,255,136,0.1)"
                ))
                fig_eq.add_hline(y=1.0, line_dash="dash", line_color="gray", annotation_text="Breakeven")
                fig_eq.update_layout(
                    title="Equity Curve (Deterministic Simulation)", template="plotly_dark",
                    yaxis_title="Cumulative Return (×)", height=350,
                )
                st.plotly_chart(fig_eq, use_container_width=True)

            with col_dd:
                fig_dd = go.Figure()
                fig_dd.add_trace(go.Scatter(
                    y=[-d for d in drawdown], mode="lines", name="Drawdown %",
                    line=dict(color="#ff4444", width=2),
                    fill="tozeroy", fillcolor="rgba(255,68,68,0.15)"
                ))
                fig_dd.add_hline(y=-20, line_dash="dash", line_color="orange", annotation_text="-20% Kill")
                fig_dd.add_hline(y=-10, line_dash="dash", line_color="yellow", annotation_text="-10% Warning")
                fig_dd.update_layout(
                    title="Drawdown Curve", template="plotly_dark",
                    yaxis_title="Drawdown %", height=350,
                )
                st.plotly_chart(fig_dd, use_container_width=True)

        # Assumptions block
        st.caption(
            "⚠️ **Backtest assumptions:** Exit model is deterministic (quality-score based). "
            "Slippage 0.10% + buy fee 0.15% + sell fee 0.25% per round-trip. "
            "Past performance ≠ future results. See SKILL.md §③ for overfitting defenses."
        )


# =====================================================================
#  TAB 3: STOCK DRILL-DOWN
# =====================================================================
with tab_drilldown:
    st.subheader("🔍 Stock Drill-Down")

    df = load_screener_data()
    if df.empty:
        st.warning("No screener data. Run `python screener.py` first.")
    else:
        # Filters
        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        with col_f1:
            signal_filter = st.multiselect(
                "Signal", ["ULTRA_BUY", "STRONG_BUY", "BUY", "PANTAU", "TUNGGU", "HINDARI"],
                default=["ULTRA_BUY", "STRONG_BUY", "BUY"],
            )
        with col_f2:
            if "Sektor" in df.columns:
                sectors = sorted(df["Sektor"].dropna().unique())
                sector_filter = st.multiselect("Sector", sectors, default=[])
            else:
                sector_filter = []
        with col_f3:
            if "MM_Activity" in df.columns:
                mm_filter = st.multiselect(
                    "MM Activity", ["ACCUMULATION", "DISTRIBUTION", "NEUTRAL"],
                    default=[],
                )
            else:
                mm_filter = []
        with col_f4:
            if "Confidence%" in df.columns:
                min_conf = st.slider("Min Confidence%", 0, 100, 40)
            else:
                min_conf = 0

        # Search
        search = st.text_input("🔎 Search Ticker", placeholder="e.g., BBCA")

        # Apply filters
        filtered = df.copy()
        if "Tanggal" in filtered.columns:
            filtered = filtered[filtered["Tanggal"] == filtered["Tanggal"].max()]
        if signal_filter:
            filtered = filtered[filtered["Sinyal"].isin(signal_filter)]
        if sector_filter:
            filtered = filtered[filtered["Sektor"].isin(sector_filter)]
        if mm_filter:
            filtered = filtered[filtered["MM_Activity"].isin(mm_filter)]
        if min_conf > 0 and "Confidence%" in filtered.columns:
            filtered = filtered[filtered["Confidence%"] >= min_conf]
        if search:
            filtered = filtered[filtered["Ticker"].astype(str).str.contains(search.upper(), na=False)]

        st.caption(f"Showing {len(filtered)} of {len(df)} records")

        # Select columns to show
        display_cols = [
            "Ticker", "Sektor", "Harga", "Skor", "Sinyal", "Confidence%",
            "RSI", "ADX", "Stoch", "MACD", "CCI", "BB_Width%",
            "Stop_Loss", "Target_1", "RRR", "Regime",
            "MM_Activity", "MM_Confidence", "Dominance", "AI_Win_Prob%", "AI_Verdict",
            "Weekly_Trend", "Monthly_Trend", "Foreign_Status",
        ]
        available_cols = [c for c in display_cols if c in filtered.columns]
        st.dataframe(
            filtered[available_cols].sort_values("Skor", ascending=False) if "Skor" in available_cols else filtered[available_cols],
            use_container_width=True,
            hide_index=True,
            height=500,
        )

        # Quick stats
        if not filtered.empty:
            st.divider()
            col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
            with col_s1:
                st.metric("Avg Skor", f"{filtered['Skor'].mean():.1f}" if "Skor" in filtered.columns else "—")
            with col_s2:
                st.metric("Avg Confidence", f"{filtered['Confidence%'].mean():.0f}%" if "Confidence%" in filtered.columns else "—")
            with col_s3:
                st.metric("Avg RRR", f"{filtered['RRR'].mean():.2f}" if "RRR" in filtered.columns else "—")
            with col_s4:
                st.metric("Avg AI Win Prob", f"{filtered['AI_Win_Prob%'].mean():.1f}%" if "AI_Win_Prob%" in filtered.columns else "—")
            with col_s5:
                acc = len(filtered[filtered["MM_Activity"] == "ACCUMULATION"]) if "MM_Activity" in filtered.columns else 0
                dist = len(filtered[filtered["MM_Activity"] == "DISTRIBUTION"]) if "MM_Activity" in filtered.columns else 0
                st.metric("MM Acc/Dist", f"{acc}/{dist}")

            # Sector breakdown
            if "Sektor" in filtered.columns:
                st.divider()
                st.subheader("📊 Sector Breakdown")
                sector_counts = filtered["Sektor"].value_counts().reset_index()
                sector_counts.columns = ["Sector", "Count"]
                fig_sector = px.bar(
                    sector_counts, x="Sector", y="Count",
                    title="Signals by Sector", color="Count",
                    color_continuous_scale="viridis",
                )
                st.plotly_chart(fig_sector, use_container_width=True)


# =====================================================================
#  TAB 4: WALK-FORWARD + AI
# =====================================================================
with tab_wf:
    st.subheader("🧪 Walk-Forward Optimization (SKILL.md §③)")
    with st.spinner("Running walk-forward optimization..."):
        df_wf = load_screener_data()
        wf_result = {}
        if df_wf.empty:
            wf_result = {"error": "No screener data. Run `python screener.py` to generate CSV files."}
        else:
            try:
                wf_result = run_walk_forward()
            except Exception as wf_e:
                wf_result = {"error": str(wf_e)}

    if "error" in wf_result:
        err_msg = str(wf_result["error"])
        st.info(f"Walk-forward: {err_msg}")
        if "Need ≥6 days" in err_msg or "float" in err_msg.lower() or "division by zero" in err_msg.lower():
            st.caption(
                "💡 Walk-forward tidak bisa berjalan. Kemungkinan penyebab:\n"
                "• Data < 6 hari — jalankan `python screener.py` setiap hari\n"
                "• Semua signal di backtest hasilkan 0 trades — tidak cukup sinyal BUY\n"
                "• Semua trade return identik → std_dev = 0 → Sharpe tidak bisa dihitung"
            )
        elif "backtest.py" in err_msg:
            st.caption("💡 Module backtest.py tidak ditemukan.")
        else:
            st.caption("💡 Pastikan kamu sudah menjalankan `python screener.py` minimal 6× di hari berbeda.")
    else:
        col_w1, col_w2, col_w3, col_w4 = st.columns(4)
        with col_w1:
            st.metric("Best SL Multiplier", f"{wf_result.get('best_sl_mult', 0):.2f}")
        with col_w2:
            st.metric("Best TP Multiplier", f"{wf_result.get('best_tp_mult', 0):.2f}")
        with col_w3:
            st.metric("Windows", wf_result.get("n_windows", 0))
        with col_w4:
            st.metric("Positive Windows", wf_result.get("positive_windows", 0))

        # Window details
        results = wf_result.get("results", [])
        if results:
            wf_df = pd.DataFrame(results)
            if "test_sharpe" in wf_df.columns:
                wf_cols = ["train_start", "train_end", "test_start", "test_end", "test_sharpe", "test_win_rate"]
                wf_cols_avail = [c for c in wf_cols if c in wf_df.columns]
                st.dataframe(wf_df[wf_cols_avail], use_container_width=True, hide_index=True)

                # Window Sharpe chart
                fig_wf = px.bar(
                    wf_df, x="test_start", y="test_sharpe",
                    title="Sharpe Ratio by Test Window",
                    color="test_sharpe",
                    color_continuous_scale=["red", "yellow", "green"],
                    color_continuous_midpoint=0,
                )
                fig_wf.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_wf.add_hline(y=1.0, line_dash="dot", line_color="green", annotation_text="Sharpe=1")
                st.plotly_chart(fig_wf, use_container_width=True)

    st.divider()

    # ── AI Prediction Summary ──────────────────────────────────────
    st.subheader("🤖 AI Prediction Overview")
    with st.spinner("Loading AI prediction data..."):
        df_ai = load_screener_data()
    if not df_ai.empty and "AI_Win_Prob%" in df_ai.columns:
        latest = df_ai[df_ai["Tanggal"] == df_ai["Tanggal"].max()] if "Tanggal" in df_ai.columns else df_ai
        ai_buy = latest[latest["Sinyal"].isin(["ULTRA_BUY", "STRONG_BUY", "BUY"])]

        if not ai_buy.empty:
            col_a1, col_a2, col_a3 = st.columns(3)
            with col_a1:
                avg_prob = ai_buy["AI_Win_Prob%"].mean()
                st.metric("Avg Win Probability", f"{avg_prob:.1f}%")
            with col_a2:
                ultra_buy_ai = ai_buy[ai_buy["AI_Verdict"] == "ULTRA BUY"] if "AI_Verdict" in ai_buy.columns else pd.DataFrame()
                st.metric("ULTRA BUY (AI)", len(ultra_buy_ai))
            with col_a3:
                weak_ai = ai_buy[ai_buy["AI_Verdict"] == "WEAK"] if "AI_Verdict" in ai_buy.columns else pd.DataFrame()
                st.metric("AI WEAK (skip)", len(weak_ai))

            # Histogram of AI win probabilities
            fig_ai = px.histogram(
                ai_buy, x="AI_Win_Prob%", nbins=20,
                title="AI Win Probability Distribution",
                color_discrete_sequence=["#636efa"],
            )
            fig_ai.add_vline(x=60, line_dash="dash", line_color="green", annotation_text="ULTRA threshold")
            fig_ai.add_vline(x=50, line_dash="dash", line_color="orange", annotation_text="BUY threshold")
            fig_ai.update_layout(template="plotly_dark")
            st.plotly_chart(fig_ai, use_container_width=True)

            # AI verdict breakdown
            if "AI_Verdict" in ai_buy.columns:
                verdict_counts = ai_buy["AI_Verdict"].value_counts().reset_index()
                verdict_counts.columns = ["Verdict", "Count"]
                fig_verdict = px.pie(
                    verdict_counts, names="Verdict", values="Count",
                    title="AI Verdict Distribution",
                    color="Verdict",
                    color_discrete_map={"ULTRA BUY": "#00ff88", "BUY": "#636efa", "WEAK": "#ffaa00"},
                )
                st.plotly_chart(fig_verdict, use_container_width=True)
        else:
            # Fallback: show AI for ALL signals with predictions (not just BUY)
            ai_all = latest[latest["AI_Win_Prob%"] > 0] if "AI_Win_Prob%" in latest.columns else pd.DataFrame()
            if not ai_all.empty:
                st.info("Tidak ada sinyal BUY, tapi AI predictions tersedia:")
                col_a1, col_a2 = st.columns(2)
                with col_a1:
                    st.metric("Total AI Predictions", len(ai_all))
                with col_a2:
                    st.metric("Avg Win Probability", f"{ai_all['AI_Win_Prob%'].mean():.1f}%")
                fig_ai_all = px.histogram(
                    ai_all, x="AI_Win_Prob%", nbins=20,
                    title="AI Win Probability Distribution (All Signals)",
                    color_discrete_sequence=["#636efa"],
                )
                fig_ai_all.add_vline(x=60, line_dash="dash", line_color="green")
                fig_ai_all.add_vline(x=50, line_dash="dash", line_color="orange")
                fig_ai_all.update_layout(template="plotly_dark")
                st.plotly_chart(fig_ai_all, use_container_width=True)
            else:
                st.info("No AI predictions yet. Run `python screener.py` to generate predictions.")
    else:
        st.info("No AI prediction data. Run screener.py with ensemble_model.pkl available.")

    # ── Parameter Sensitivity note ────────────────────────────────
    st.divider()
    st.caption(
        "📐 **Overfitting defenses per SKILL.md §③:** Walk-forward IS/OOS windows, "
        "parameter grid search with SL [1.0–2.0] × TP [1.5–3.0] ATR multipliers. "
        "Monte Carlo & parameter sensitivity heatmap available via `monte_carlo.py`."
    )


# =====================================================================
#  TAB 5: ALERTS & NOTIFICATIONS
# =====================================================================
with tab_alerts:
    st.subheader("📡 Alerts & Notifications")

    # Alert manager config
    col_alert1, col_alert2 = st.columns(2)

    with col_alert1:
        st.markdown("### 🔔 Discord")
        discord_webhook = os.getenv("DISCORD_WEBHOOK", "")
        if discord_webhook:
            st.success("✅ Discord configured")
            st.caption(f"Webhook: {discord_webhook[:50]}...")
        else:
            st.warning("⚠️ Discord not configured. Set DISCORD_WEBHOOK in .env")
            new_webhook = st.text_input("Enter Discord webhook URL:", type="password")
            if new_webhook:
                st.info("Add `DISCORD_WEBHOOK=your_webhook_url` to .env file")

    with col_alert2:
        st.markdown("### 📱 Telegram")
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if telegram_token and telegram_chat_id:
            st.success("✅ Telegram configured")
            st.caption(f"Chat ID: {telegram_chat_id}")
        else:
            st.warning("⚠️ Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")

    st.divider()

    # Alert thresholds display
    st.markdown("### ⚠️ Alert Thresholds")
    thresholds_df = pd.DataFrame([
        {"Trigger": "Daily Drawdown > 3%", "Level": "WARNING", "Channel": "Discord + Telegram", "Action": "Alert only"},
        {"Trigger": "Daily Drawdown > 5%", "Level": "CRITICAL", "Channel": "All channels", "Action": "HALT trading"},
        {"Trigger": "Unrealized Loss > 2%", "Level": "WARNING", "Channel": "Telegram", "Action": "Per-position alert"},
        {"Trigger": "API Connection Error", "Level": "WARNING", "Channel": "Telegram", "Action": "Immediate alert + reconnect"},
        {"Trigger": "Kill Switch Triggered", "Level": "CRITICAL", "Channel": "ALL CHANNELS", "Action": "Urgent — stop all trading"},
    ])
    st.dataframe(thresholds_df, use_container_width=True, hide_index=True)

    st.divider()

    # Test alert + Screener report
    st.markdown("### 📤 Send Alerts")

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("🚀 Send Test Alert", use_container_width=True):
            try:
                from dashboard.alerts import AlertManager
                am = AlertManager()
                ok = am.send("INFO", "Dashboard Test", "This is a test alert from the Quant Trader dashboard.")
                if ok:
                    st.success("Test alert sent!")
                else:
                    st.warning("No channels available — check .env")
            except Exception as e:
                st.error(f"Failed: {e}")

    with col_btn2:
        if st.button("📊 Send Screener Report", use_container_width=True):
            try:
                from dashboard.alerts import AlertManager
                df = load_screener_data()
                if df.empty:
                    st.warning("No screener data. Run `python screener.py` first.")
                else:
                    latest = df[df["Tanggal"] == df["Tanggal"].max()] if "Tanggal" in df.columns else df
                    # Available columns (some may be missing in older CSV files)
                    _cols_available = [c for c in ["Ticker", "Harga", "Skor", "Confidence%", "RRR", "AI_Verdict"]
                                       if c in latest.columns]
                    # Build signal lists — use only columns that exist
                    ultra = latest[latest["Sinyal"] == "ULTRA_BUY"][_cols_available].to_dict("records") \
                        if "Sinyal" in latest.columns else []
                    strong = latest[latest["Sinyal"] == "STRONG_BUY"][_cols_available].to_dict("records") \
                        if "Sinyal" in latest.columns else []
                    buy_list = latest[latest["Sinyal"] == "BUY"][_cols_available].to_dict("records") \
                        if "Sinyal" in latest.columns else []

                    am = AlertManager()
                    result = am.send_screener_report(ultra, strong, buy_list)
                    st.success(result)
            except Exception as e:
                st.error(f"Failed: {e}")

    # Alert log
    st.divider()
    st.markdown("### 📋 Recent Alert Log")
    log_dir = os.path.join(_PROJECT_ROOT, "logs")
    log_file = os.path.join(log_dir, f"screener_{datetime.now():%Y%m%d}.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # Filter for WARNING/CRITICAL lines
            alert_lines = [l for l in lines[-100:] if "WARNING" in l or "CRITICAL" in l or "KILL SWITCH" in l]
            if alert_lines:
                for line in reversed(alert_lines[-30:]):
                    if "CRITICAL" in line or "KILL SWITCH" in line:
                        st.error(line.strip())
                    elif "WARNING" in line:
                        st.warning(line.strip())
                    else:
                        st.text(line.strip())
            else:
                st.info("No alerts today.")
        except Exception:
            st.info("Could not read log file.")
    else:
        st.info("No log file for today yet.")

# =====================================================================
#  TAB 6: SCALPING
# =====================================================================
with tab_scalp:
    st.subheader("⚡ Scalping — Live Monitor")

    # ── Helpers for scalp data ────────────────────────────────────
    @st.cache_data(ttl=5)
    def _load_scalp_signals() -> pd.DataFrame:
        db = os.path.join(_PROJECT_ROOT, "histori_ihsg.db")
        if not os.path.exists(db):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(db)
            df = pd.read_sql(
                "SELECT * FROM sinyal_trading ORDER BY waktu DESC LIMIT 50",
                conn,
            )
            conn.close()
            return df
        except Exception:
            return pd.DataFrame()

    @st.cache_data(ttl=5)
    def _load_scalp_positions() -> pd.DataFrame:
        db = os.path.join(_PROJECT_ROOT, "portofolio_virtual.db")
        if not os.path.exists(db):
            return pd.DataFrame()
        try:
            conn = sqlite3.connect(db)
            df = pd.read_sql(
                "SELECT ticker, harga_beli, sl, tp, shares, highest_price, strategy "
                "FROM posisi WHERE strategy='scalp'",
                conn,
            )
            conn.close()
            return df
        except Exception:
            return pd.DataFrame()

    @st.cache_data(ttl=10)
    def _load_scalp_pnl() -> dict:
        db = os.path.join(_PROJECT_ROOT, "portofolio_virtual.db")
        if not os.path.exists(db):
            return {"today_pnl": 0, "today_trades": 0, "today_wins": 0}
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(db)
            df = pd.read_sql(
                "SELECT pnl, status FROM histori_trade WHERE strategy='scalp' AND tanggal=?",
                conn, params=[today],
            )
            conn.close()
            if df.empty:
                return {"today_pnl": 0, "today_trades": 0, "today_wins": 0}
            return {
                "today_pnl": int(df["pnl"].sum()),
                "today_trades": len(df),
                "today_wins": int((df["pnl"] > 0).sum()),
            }
        except Exception:
            return {"today_pnl": 0, "today_trades": 0, "today_wins": 0}

    # ── Row 1: Scalp PnL Summary ──────────────────────────────────
    pnl_data = _load_scalp_pnl()
    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    with col_s1:
        st.metric(
            "⚡ Today's Scalp PnL",
            f"Rp {pnl_data['today_pnl']:+,.0f}",
        )
    with col_s2:
        st.metric("📊 Scalp Trades Today", pnl_data["today_trades"])
    with col_s3:
        wr = (pnl_data["today_wins"] / max(1, pnl_data["today_trades"]) * 100)
        st.metric("🏆 Win Rate Today", f"{wr:.0f}%")
    with col_s4:
        from scalp.config import ScalpConfig
        scfg = ScalpConfig.from_yaml()
        st.metric("⚙️ TP/SL", f"{scfg.tp_pct*100:.1f}% / {scfg.sl_pct*100:.1f}%")

    st.divider()

    # ── Row 2: Open Scalp Positions ───────────────────────────────
    st.subheader("📋 Open Scalp Positions")
    pos_df = _load_scalp_positions()
    if not pos_df.empty:
        # Add trailing stop status
        rows = []
        for _, p in pos_df.iterrows():
            profit_pct = 0.0
            if p["harga_beli"] > 0:
                profit_pct = (p["highest_price"] - p["harga_beli"]) / p["harga_beli"] * 100
            trailing_active = "✅" if profit_pct >= scfg.trailing_activation_pct * 100 else ("🟡" if profit_pct >= scfg.breakeven_trigger_pct * 100 else "⏳")
            rows.append({
                "Ticker": p["ticker"],
                "Entry": f"Rp {int(p['harga_beli']):,}",
                "SL": f"Rp {int(p['sl']):,}",
                "TP": f"Rp {int(p['tp']):,}",
                "Shares": int(p["shares"]),
                "Peak": f"Rp {int(p['highest_price']):,}",
                "Profit%": f"{profit_pct:+.2f}%",
                "Trail": trailing_active,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No open scalp positions. Run `python -m scalp.run executor` during market hours.")

    st.divider()

    # ── Row 3: Recent Scalp Signals ───────────────────────────────
    st.subheader("📡 Recent Scalp Signals")
    sig_df = _load_scalp_signals()
    if not sig_df.empty:
        st.dataframe(
            sig_df[["ticker", "harga", "sinyal", "tp", "sl", "confidence", "waktu"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No scalp signals yet. Run `python -m scalp.run executor` during market hours.")

    st.divider()

    # ── Row 4: Scalp Config Summary ────────────────────────────────
    st.caption(
        f"⚙️ **Scalp Config:** TP {scfg.tp_pct*100:.1f}% | SL {scfg.sl_pct*100:.1f}% | "
        f"Breakeven {scfg.breakeven_trigger_pct*100:.1f}% | Trail {scfg.trailing_distance_pct*100:.1f}% "
        f"after {scfg.trailing_activation_pct*100:.1f}% | "
        f"Daily loss halt {scfg.max_daily_loss_pct*100:.0f}% | Max {scfg.max_positions} positions | "
        f"Capital Rp {scfg.capital_initial:,.0f}"
    )


# ── Footer ────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "⚠️ **Disclaimer:** All strategies and data are for educational and research purposes only. "
    "Past backtest performance does not guarantee future live results. "
    "Trade only capital you can afford to lose. | Built with SKILL.md §①–§⑥"
)
