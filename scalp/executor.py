# scalp/executor.py — Paper Trading Executor for Scalping (SKILL.md §④)
# =====================================================================
# Migrated from 3_consumer_r1.py with:
#   - Shared AlertManager (no own Discord code)
#   - Shared KillSwitch (no duplicate risk checks)
#   - Unified DB schema via src/data/schema.py
#   - Config-driven via scalp/config.py (no hardcoded params)
#   - Uses scalp/signals.py + scalp/ai.py for signal generation
#   - Trailing stop: breakeven after 0.8% + trail 0.5% after 1.5%
#
# Run: python -m scalp.run executor

from __future__ import annotations

import logging
import os as _os
import sqlite3
import sys
import time
from datetime import datetime, date

# ── Path setup ───────────────────────────────────────────────────────
_sys_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _sys_root not in sys.path:
    sys.path.insert(0, _sys_root)

from scalp.config import ScalpConfig
from scalp.signals import (
    is_trading_allowed,
    get_session,
    compute_intraday_features,
    build_signal,
)
from scalp.ai import predict_scalp_signal, filter_signals_with_ai
from src.data.schema import init_histori_db, init_portfolio_db, get_or_create_state, set_state
from risk.kill_switch import KillSwitch
from dashboard.alerts import AlertManager

# ── Logging ──────────────────────────────────────────────────────────
_os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] executor: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("logs/scalp_executor.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("scalp.executor")

# ── Config ───────────────────────────────────────────────────────────
config = ScalpConfig.from_yaml()
kill_switch = KillSwitch()
alert_mgr = AlertManager()

HISTORI_DB = _os.path.join(_sys_root, config.histori_db_name)
PORTFOLIO_DB = _os.path.join(_sys_root, config.portfolio_db_name)

# Daily state tracking
_daily_state = {
    "date": "",
    "realized_pnl": 0.0,
    "trades": 0,
    "session_start_equity": config.capital_initial,
    "peak_equity": config.capital_initial,
}

# Cooldown tracking: ticker → last trade timestamp
_last_trade_time: dict[str, float] = {}


# ── Helpers ──────────────────────────────────────────────────────────

def _reset_daily_if_new_day(current_equity: float) -> None:
    today = date.today().isoformat()
    if _daily_state["date"] != today:
        _daily_state["date"] = today
        _daily_state["realized_pnl"] = 0.0
        _daily_state["trades"] = 0
        _daily_state["session_start_equity"] = current_equity


def _read_equity(conn: sqlite3.Connection) -> float:
    """Read total portfolio equity (cash + positions)."""
    cur = conn.cursor()
    cash_row = cur.execute("SELECT saldo_cash FROM akun").fetchone()
    cash = cash_row[0] if cash_row else 0.0
    pos = cur.execute("SELECT ticker, harga_beli, shares FROM posisi").fetchall()
    positions_value = sum(p[1] * p[2] for p in pos)
    return cash + positions_value


def _check_limits(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Pre-trade risk checks: kill switch, daily loss, max positions."""
    equity = _read_equity(conn)
    _reset_daily_if_new_day(equity)

    # Kill switch
    peak = _daily_state.get("peak_equity", config.capital_initial)
    session_start = _daily_state.get("session_start_equity", config.capital_initial)
    ks_ok, ks_reason = kill_switch.check(equity, peak, session_start)
    if not ks_ok:
        return False, f"KILL SWITCH: {ks_reason}"

    # Daily loss (% of session start)
    daily_pnl = _daily_state["realized_pnl"]
    session_eq = _daily_state["session_start_equity"]
    # FIX: Use session_start_equity (not CAPITAL_INITIAL)
    daily_pnl_pct = abs(daily_pnl) / session_eq if session_eq > 0 else 0
    if daily_pnl <= 0 and daily_pnl_pct >= config.max_daily_loss_pct:
        return False, f"Max daily loss {config.max_daily_loss_pct*100:.0f}% reached"

    # Max positions
    cur = conn.cursor()
    open_count = cur.execute("SELECT COUNT(*) FROM posisi").fetchone()[0]
    if open_count >= config.max_positions:
        return False, f"Max positions ({config.max_positions}) reached"

    # Cooldown check done per-ticker in _check_cooldown()

    # Update peak
    if equity > _daily_state["peak_equity"]:
        _daily_state["peak_equity"] = equity

    return True, "OK"


def _check_cooldown(ticker: str) -> bool:
    """Check if ticker is still in cooldown period."""
    last = _last_trade_time.get(ticker, 0)
    cooldown_secs = config.cooldown_minutes * 60
    return (time.time() - last) >= cooldown_secs


def _record_trade(ticker: str) -> None:
    """Record trade for cooldown tracking."""
    _last_trade_time[ticker] = time.time()


# ── Portfolio Initialization ─────────────────────────────────────────

def init_portfolio() -> sqlite3.Connection:
    """Initialize portfolio database and return connection."""
    conn = sqlite3.connect(PORTFOLIO_DB)
    init_portfolio_db(conn, config.capital_initial)
    return conn


# ── Signal Fetching ──────────────────────────────────────────────────

def _fetch_signal(ticker: str, hist_conn: sqlite3.Connection) -> dict | None:
    """Fetch and analyze a single ticker from SQLite.

    Returns signal dict or None if no tradeable signal.
    """
    cur = hist_conn.cursor()
    cur.execute(
        "SELECT open, high, low, harga, volume FROM histori_ihsg "
        "WHERE ticker = ? ORDER BY id DESC LIMIT 60",
        (ticker,),
    )
    rows = cur.fetchall()
    if len(rows) < 5:
        return None

    # Reverse to chronological order
    rows = list(reversed(rows))
    import pandas as pd

    open_p = pd.Series([r[0] or r[3] or 0 for r in rows], dtype=float)
    high_p = pd.Series([r[1] or r[3] or 0 for r in rows], dtype=float)
    low_p = pd.Series([r[2] or r[3] or 0 for r in rows], dtype=float)
    close_p = pd.Series([r[3] or 0 for r in rows], dtype=float)
    vol_p = pd.Series([r[4] or 0 for r in rows], dtype=float)

    result = build_signal(ticker, open_p, high_p, low_p, close_p, vol_p, config)
    if result is None:
        return None

    # Run AI prediction
    pred = predict_scalp_signal(result, config)
    if pred.win_prob_pct < config.ai_confidence_threshold:
        return None

    # Store signal in DB
    cur.execute(
        "INSERT INTO sinyal_trading (ticker, harga, sinyal, tp, sl, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            ticker,
            result.entry_price,
            result.signal,
            result.take_profit,
            result.stop_loss,
            pred.win_prob_pct,
        ),
    )
    hist_conn.commit()

    return {
        "ticker": ticker,
        "signal": result.signal,
        "entry": result.entry_price,
        "sl": result.stop_loss,
        "tp": result.take_profit,
        "rrr": result.rrr,
        "confidence": pred.win_prob_pct,
        "strategy": result.strategy,
    }


# ── Trade Execution ──────────────────────────────────────────────────

def execute_buy(signal: dict, port_conn: sqlite3.Connection) -> bool:
    """Execute a paper buy order. Returns True on success."""
    ticker = signal["ticker"]
    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]

    # Position sizing: 10% of equity
    cur = port_conn.cursor()
    equity = _read_equity(port_conn)
    max_buy = equity * config.position_size_pct

    shares = int(max_buy / entry / 100) * 100  # Lot size = 100 shares
    if shares < 100:
        logger.debug("%s: position too small (%d shares)", ticker, shares)
        return False

    # Apply buy fee
    total_cost = shares * entry * (1 + config.buy_fee_pct)
    cash_row = cur.execute("SELECT saldo_cash FROM akun").fetchone()
    cash = cash_row[0] if cash_row else 0

    if total_cost > cash:
        logger.debug("%s: insufficient cash (need Rp%.0f, have Rp%.0f)",
                     ticker, total_cost, cash)
        return False

    # Update cash
    cur.execute("UPDATE akun SET saldo_cash = saldo_cash - ?", (total_cost,))
    # Insert position
    cur.execute(
        "INSERT INTO posisi (ticker, harga_beli, sl, tp, shares, tanggal, strategy) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, entry, sl, tp, shares, date.today().isoformat(), "scalp"),
    )
    port_conn.commit()

    _record_trade(ticker)
    _daily_state["trades"] += 1

    # Alert
    details = (
        f"🎯 *SCALP {signal['signal']}*\n"
        f"Entry: Rp {entry:,.0f} | {shares} shares\n"
        f"SL: Rp {sl:,.0f} | TP: Rp {tp:,.0f} | RRR: {signal['rrr']}\n"
        f"AI: {signal['confidence']:.1f}% | {signal['strategy']}"
    )
    alert_mgr.send("INFO", f"SCALP: {ticker}", details)
    logger.info("BUY %s | Entry=Rp%.0f | %d shares | SL=Rp%.0f | TP=Rp%.0f",
                ticker, entry, shares, sl, tp)
    return True


# ── Position Monitor (Trailing Stop) ─────────────────────────────────

def monitor_positions(hist_conn: sqlite3.Connection, port_conn: sqlite3.Connection) -> None:
    """Check open positions and apply trailing stop / exit logic."""
    cur_port = port_conn.cursor()
    cur_port.execute(
        "SELECT rowid, ticker, harga_beli, sl, tp, shares, highest_price FROM posisi"
    )
    positions = cur_port.fetchall()

    if not positions:
        return

    cur_hist = hist_conn.cursor()

    for pos in positions:
        rowid, tkr, h_beli, sl, tp, shares, highest_price = pos
        highest_price = highest_price or h_beli

        # Get latest price
        cur_hist.execute(
            "SELECT open, high, low, harga FROM histori_ihsg "
            "WHERE ticker = ? ORDER BY id DESC LIMIT 3",
            (tkr,),
        )
        rows = cur_hist.fetchall()
        if not rows:
            continue

        harga_live = rows[0][3] if rows[0][3] else rows[0][0] or 0
        high_live = max(r[1] or r[3] or 0 for r in rows)

        if harga_live <= 0:
            continue

        # Update highest price
        if high_live > highest_price:
            highest_price = high_live

        # Trailing stop logic
        new_sl = sl
        profit_pct = (harga_live - h_beli) / h_beli if h_beli > 0 else 0

        # Breakeven: after breakeven_trigger_pct profit, SL = entry
        if profit_pct >= config.breakeven_trigger_pct and sl < h_beli:
            new_sl = h_beli

        # Trailing: after trailing_activation_pct profit, trail SL
        if profit_pct >= config.trailing_activation_pct:
            trail_sl = highest_price * (1 - config.trailing_distance_pct)
            new_sl = max(new_sl, trail_sl)

        # Update SL in DB if changed
        if new_sl != sl:
            cur_port.execute(
                "UPDATE posisi SET sl = ?, highest_price = ? WHERE rowid = ?",
                (new_sl, highest_price, rowid),
            )

        # Check exit
        exit_price = harga_live
        exit_reason = None

        if harga_live >= tp:
            exit_reason = "TAKE PROFIT"
        elif harga_live <= new_sl:
            exit_reason = "CUT LOSS"
            exit_price = new_sl

        if exit_reason:
            # Apply slippage
            exit_real = exit_price * (1 - config.slippage_pct)

            # Calculate PnL
            gross_sell = exit_real * shares
            sell_fee = gross_sell * config.sell_fee_pct
            net_sell = gross_sell - sell_fee

            gross_buy = h_beli * shares
            buy_fee = gross_buy * config.buy_fee_pct
            total_cost = gross_buy + buy_fee

            pnl_net = net_sell - total_cost
            pnl_pct = (pnl_net / total_cost * 100) if total_cost > 0 else 0

            # Update portfolio
            cur_port.execute("UPDATE akun SET saldo_cash = saldo_cash + ?", (net_sell,))
            cur_port.execute("DELETE FROM posisi WHERE rowid = ?", (rowid,))
            cur_port.execute(
                "INSERT INTO histori_trade (ticker, pnl, status, tanggal, strategy) "
                "VALUES (?, ?, ?, ?, ?)",
                (tkr, pnl_net, exit_reason, date.today().isoformat(), "scalp"),
            )
            port_conn.commit()

            _daily_state["realized_pnl"] += pnl_net

            # Alert
            level = "INFO" if pnl_net > 0 else "WARNING"
            emoji = "✅" if pnl_net > 0 else "❌"
            details = (
                f"{emoji} *{exit_reason}*\n"
                f"{tkr} | PnL: Rp {pnl_net:+,.0f} ({pnl_pct:+.2f}%)\n"
                f"Entry: Rp {h_beli:,.0f} → Exit: Rp {exit_real:,.0f}\n"
                f"Equity: Rp {_read_equity(port_conn):,.0f}"
            )
            alert_mgr.send(level, f"SCALP EXIT: {tkr}", details)

            # Unrealized loss alert
            if pnl_pct <= -2.0:
                alert_mgr.unrealized_loss_alert(tkr, abs(pnl_pct) / 100)

            logger.info("EXIT %s | %s | PnL=Rp%.0f (%.2f%%) | Equity=Rp%.0f",
                        tkr, exit_reason, pnl_net, pnl_pct,
                        _read_equity(port_conn))


# ── Main Executor Loop ───────────────────────────────────────────────

def executor_loop(hist_conn: sqlite3.Connection, port_conn: sqlite3.Connection) -> None:
    """Main executor loop: monitor positions → scan for new signals."""
    logger.info("SCALP EXECUTOR v3.0 STARTING")
    logger.info("  Capital: Rp%,.0f | Max pos: %d | TP: %.1f%% | SL: %.1f%%",
                config.capital_initial, config.max_positions,
                config.tp_pct * 100, config.sl_pct * 100)
    logger.info("  Breakeven: %.1f%% | Trail: %.1f%% after %.1f%%",
                config.breakeven_trigger_pct * 100,
                config.trailing_distance_pct * 100,
                config.trailing_activation_pct * 100)

    while True:
        try:
            # ── Always monitor open positions ───────────────────────
            monitor_positions(hist_conn, port_conn)

            # ── Check if trading allowed ────────────────────────────
            allowed, reason = is_trading_allowed(config)
            if not allowed:
                time.sleep(config.poll_idle_secs)
                continue

            # ── Risk limits ─────────────────────────────────────────
            limits_ok, limit_reason = _check_limits(port_conn)
            if not limits_ok:
                if "KILL SWITCH" in limit_reason:
                    alert_mgr.kill_switch_triggered(limit_reason)
                    logger.critical("KILL SWITCH TRIGGERED — halting executor")
                    break  # Fatal — stop the executor
                logger.debug("Limits: %s", limit_reason)
                time.sleep(config.poll_idle_secs)
                continue

            # ── Scan for new signals ────────────────────────────────
            cur = hist_conn.cursor()
            cur.execute(
                "SELECT DISTINCT ticker FROM histori_ihsg "
                "WHERE waktu >= datetime('now', '-5 minutes') "
                "ORDER BY ticker"
            )
            active_tickers = [r[0] for r in cur.fetchall()]

            for ticker in active_tickers[:config.max_concurrent]:
                if not _check_cooldown(ticker):
                    continue

                signal = _fetch_signal(ticker, hist_conn)
                if signal:
                    execute_buy(signal, port_conn)

            # ── Sleep ───────────────────────────────────────────────
            time.sleep(config.poll_fast_secs)

        except KeyboardInterrupt:
            logger.info("Executor stopped by user")
            break
        except Exception as e:
            logger.error("Executor error: %s", e, exc_info=True)
            alert_mgr.api_error("scalp_executor", str(e))
            time.sleep(config.poll_idle_secs)


def run_executor() -> None:
    """Synchronous entry point for the executor."""
    port_conn = init_portfolio()
    hist_conn = sqlite3.connect(HISTORI_DB)
    init_histori_db(hist_conn)

    try:
        executor_loop(hist_conn, port_conn)
    finally:
        hist_conn.close()
        port_conn.close()


if __name__ == "__main__":
    run_executor()
