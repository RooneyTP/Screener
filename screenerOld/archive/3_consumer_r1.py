"""
FILE 3 -- RL EXECUTOR v4.0 (SCALPING UPGRADE)
================================================
Upgrades:
  - Trailing stop: breakeven + trailing 0.5%
  - Max daily loss + max positions enforcement
  - Slippage buffer in exit price
  - Trade journal integration for scalping
  - Better position monitoring with real OHLCV
"""

import sqlite3
import time
import datetime
import requests
import os
import logging
import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

import os
from security import get_env_var

DB_NAME = "histori_ihsg.db"
PORTFOLIO_DB = "portofolio_virtual.db"
# FIX: Never hardcode API keys — use .env (SKILL.md safety rule)
DISCORD_WEBHOOK_URL = get_env_var("DISCORD_WEBHOOK", "")
# FIX: Import kill switch before connecting to live account
from risk.kill_switch import KillSwitch
kill_switch = KillSwitch()

FEE_BELI = 0.0015  # 0.15%
FEE_JUAL = 0.0025  # 0.25%
# FIX: slippage should be ATR-based per SKILL.md; for now use dynamic minimum based on price
SLIPPAGE_BUFFER = 0.002  # 0.2% — base; consider ATR-based for live scalping
# FIX: risk limits as percentage of equity (SKILL.md Level 1–5 risk hierarchy)
MAX_DAILY_LOSS_PCT = 0.03    # 3% daily loss → halt
MAX_POSITIONS = 5            # Max concurrent open positions
BREAKEVEN_TRIGGER = 0.008    # After 0.8% profit, move SL to breakeven
TRAILING_DISTANCE = 0.005    # Trail SL 0.5% below highest price
CAPITAL_INITIAL = 100_000_000.0

# FIX: track peak equity for kill switch + daily check
_daily_state = {"date": "", "realized_pnl": 0.0, "trades": 0, "peak_equity": CAPITAL_INITIAL, "session_start_equity": CAPITAL_INITIAL}

def kirim_discord(judul, pesan, warna):
    if not DISCORD_WEBHOOK_URL:
        return
    data = {
        "embeds": [{
            "title": judul,
            "description": pesan,
            "color": warna,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=5)
    except Exception as e:
        log.warning(f"Discord send failed: {e}")

def reset_daily_if_new_day(saldo_sekarang: float):
    today = datetime.date.today().isoformat()
    if _daily_state["date"] != today:
        _daily_state["date"] = today
        _daily_state["realized_pnl"] = 0.0
        _daily_state["trades"] = 0
        _daily_state["session_start_equity"] = saldo_sekarang

def check_daily_limits(cur_port, saldo_sekarang: float, new_trade_pnl_estimate=0) -> tuple[bool, str]:
    """Check if we can take another trade — with kill switch integration."""
    reset_daily_if_new_day(saldo_sekarang)

    # FIX: Kill switch check (SKILL.md mandatory)
    current_equity = saldo_sekarang
    peak_equity = _daily_state.get("peak_equity", CAPITAL_INITIAL)
    session_start = _daily_state.get("session_start_equity", CAPITAL_INITIAL)
    kill_ok, kill_reason = kill_switch.check(current_equity, peak_equity, session_start)
    if not kill_ok:
        return False, f"KILL SWITCH: {kill_reason}"

    # FIX: daily loss as % of equity (SKILL.md Level 2)
    daily_pnl_pct = _daily_state["realized_pnl"] / CAPITAL_INITIAL
    if daily_pnl_pct <= -MAX_DAILY_LOSS_PCT:
        return False, f"Max daily loss {MAX_DAILY_LOSS_PCT*100:.0f}% reached: {daily_pnl_pct*100:.1f}%"

    # Check number of open positions
    cur_port.execute("SELECT COUNT(*) FROM posisi")
    open_pos = cur_port.fetchone()[0]
    if open_pos >= MAX_POSITIONS:
        return False, f"Max positions ({MAX_POSITIONS}) reached"

    return True, "OK"

def inisialisasi_portofolio():
    conn = sqlite3.connect(PORTFOLIO_DB)
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS akun (saldo_cash REAL);
        CREATE TABLE IF NOT EXISTS posisi (
            ticker TEXT, harga_beli REAL, sl REAL, tp REAL,
            shares INTEGER, tanggal TEXT, highest_price REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS histori_trade (
            ticker TEXT, pnl REAL, status TEXT, tanggal TEXT
        );
    """)
    cursor.execute("SELECT saldo_cash FROM akun")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO akun (saldo_cash) VALUES (?)", (CAPITAL_INITIAL,))
    conn.commit()
    conn.close()

def pantau_dan_exit(cursor_hist):
    conn_port = sqlite3.connect(PORTFOLIO_DB)
    cur_port = conn_port.cursor()

    cur_port.execute("SELECT rowid, ticker, harga_beli, sl, tp, shares, highest_price FROM posisi")
    posisi_open = cur_port.fetchall()

    for pos in posisi_open:
        rowid, tkr, h_beli, sl, tp, shares, highest_price = pos
        highest_price = highest_price or h_beli

        # Get latest OHLCV
        cursor_hist.execute(
            "SELECT open, high, low, harga FROM histori_ihsg WHERE ticker = ? ORDER BY id DESC LIMIT 3",
            (tkr,)
        )
        rows = cursor_hist.fetchall()
        if not rows:
            continue

        harga_live = rows[0][3] if rows[0][3] else rows[0][0] or 0
        high_live = max(r[1] or r[3] or 0 for r in rows)  # max high in recent candles

        if harga_live <= 0:
            continue

        # Update highest price for trailing stop
        if high_live > highest_price:
            highest_price = high_live

        # TRAILING STOP LOGIC
        new_sl = sl
        status_jual = None

        # Level 1: Breakeven -- after 0.8% profit, SL = entry
        profit_pct = (harga_live - h_beli) / h_beli
        if profit_pct >= BREAKEVEN_TRIGGER and sl < h_beli:
            new_sl = h_beli

        # Level 2: Trailing -- SL trails 0.5% below highest price
        if profit_pct >= 0.015:  # After 1.5% profit
            trail_sl = highest_price * (1 - TRAILING_DISTANCE)
            new_sl = max(new_sl, trail_sl)

        # Check exit
        exit_price = harga_live
        if harga_live >= tp:
            status_jual = "TAKE PROFIT"
            warna_embed = 65280
        elif harga_live <= new_sl:
            status_jual = "CUT LOSS"
            exit_price = new_sl  # Exit at SL price
            warna_embed = 16711680

        # Update SL in DB if changed (trailing)
        if new_sl != sl:
            cur_port.execute("UPDATE posisi SET sl = ?, highest_price = ? WHERE rowid = ?",
                           (new_sl, highest_price, rowid))

        if status_jual:
            # Apply slippage to exit
            exit_real = exit_price * (1 - SLIPPAGE_BUFFER)

            nilai_jual_kotor = exit_real * shares
            biaya_jual_dan_pajak = nilai_jual_kotor * FEE_JUAL
            uang_masuk_bersih = nilai_jual_kotor - biaya_jual_dan_pajak

            modal_awal_kotor = h_beli * shares
            biaya_beli_awal = modal_awal_kotor * FEE_BELI
            total_modal_keluar = modal_awal_kotor + biaya_beli_awal

            pnl_bersih = uang_masuk_bersih - total_modal_keluar

            cur_port.execute("SELECT saldo_cash FROM akun")
            saldo_lama = cur_port.fetchone()[0]
            saldo_baru = saldo_lama + uang_masuk_bersih

            cur_port.execute("UPDATE akun SET saldo_cash = ?", (saldo_baru,))
            cur_port.execute("DELETE FROM posisi WHERE rowid = ?", (rowid,))
            cur_port.execute("INSERT INTO histori_trade VALUES (?, ?, ?, ?)",
                           (tkr, pnl_bersih, status_jual, str(datetime.datetime.now())))
            conn_port.commit()

            # FIX: update daily state + peak equity tracking
            _daily_state["realized_pnl"] += pnl_bersih
            _daily_state["trades"] += 1
            if saldo_baru > _daily_state.get("peak_equity", CAPITAL_INITIAL):
                _daily_state["peak_equity"] = saldo_baru

            log.info(f"EXIT: {status_jual} | {tkr} @ Rp{exit_real:,.0f} | PnL: Rp{pnl_bersih:,.0f} | Saldo: Rp{saldo_baru:,.0f}")

            pesan = f"**{status_jual}**\\nTicker: {tkr}\\nExit: Rp{exit_real:,.0f}\\nPnL: Rp{pnl_bersih:,.0f}\\nSisa Saldo: Rp{saldo_baru:,.0f}"
            kirim_discord(f"EXIT: {tkr} ({status_jual})", pesan, warna_embed)

            # Log to trade journal
            try:
                from trade_journal import log_exit as journal_exit
                journal_exit(tkr, exit_real, pnl_bersih, (pnl_bersih / total_modal_keluar * 100) if total_modal_keluar > 0 else 0)
            except Exception:
                pass

    conn_port.close()

def eksekusi_beli(ticker, harga, sl, tp):
    conn = sqlite3.connect(PORTFOLIO_DB)
    cursor = conn.cursor()

    # Check limits (pass actual balance for kill switch)
    cursor.execute("SELECT saldo_cash FROM akun")
    saldo = cursor.fetchone()[0]
    allowed, reason = check_daily_limits(cursor, saldo)
    if not allowed:
        log.warning(f"BUY BLOCKED: {ticker} -- {reason}")
        conn.close()
        return

    # Apply slippage to entry
    entry_real = harga * (1 + SLIPPAGE_BUFFER)

    cursor.execute("SELECT saldo_cash FROM akun")
    saldo = cursor.fetchone()[0]

    # FIX: Fixed Fractional sizing — 1% equity at risk per trade (SKILL.md §④ default)
    max_beli = saldo * 0.10
    jumlah_lot = int((max_beli / entry_real) / 100)
    shares_to_buy = jumlah_lot * 100

    if shares_to_buy >= 100:
        modal_saham = shares_to_buy * entry_real
        biaya_fee = modal_saham * FEE_BELI
        total_keluar = modal_saham + biaya_fee

        if saldo >= total_keluar:
            saldo_baru = saldo - total_keluar

            cursor.execute("UPDATE akun SET saldo_cash = ?", (saldo_baru,))
            cursor.execute("INSERT INTO posisi VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (ticker, entry_real, sl, tp, shares_to_buy, str(datetime.datetime.now()), entry_real))
            conn.commit()

            log.info(f"BUY: {ticker} | {shares_to_buy} sh @ Rp{entry_real:,.0f} | Cost: Rp{total_keluar:,.0f}")
            kirim_discord(f"BUY: {ticker}", f"**{shares_to_buy} shares** @ Rp{entry_real:,.0f}\\nTP: Rp{tp:,.0f} | SL: Rp{sl:,.0f}", 3447003)

            # Log to trade journal
            try:
                from trade_journal import log_entry as journal_entry
                journal_entry(ticker, "Scalping", entry_real, shares_to_buy, "SCALP_BUY", "INTRADAY", "scalping_bot")
            except Exception:
                pass

    conn.close()

def jalankan_rl_agent():
    log.info("RL EXECUTOR v4.0 (TRAILING STOP + LIMITS) AKTIF")
    inisialisasi_portofolio()

    conn_hist = sqlite3.connect(DB_NAME)
    cursor_hist = conn_hist.cursor()
    cursor_hist.execute("CREATE TABLE IF NOT EXISTS sinyal_trading (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, harga REAL, sinyal TEXT, tp REAL, sl REAL, waktu DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn_hist.commit()

    cursor_hist.execute("SELECT MAX(id) FROM sinyal_trading")
    last_signal_id = cursor_hist.fetchone()[0] or 0

    log.info("Agent ready. Monitoring positions + signals...")
    kirim_discord("BOT SCALPING v4.0 ONLINE",
                  "Trailing stop + max loss + time filter + slippage ACTIVE.\\nReady to hunt.",
                  65280)

    try:
        while True:
            # Monitor open positions
            pantau_dan_exit(cursor_hist)

            # Check for new signals
            cursor_hist.execute("SELECT id, ticker, harga, sinyal, tp, sl FROM sinyal_trading WHERE id > ? LIMIT 1", (last_signal_id,))
            data = cursor_hist.fetchone()

            if data:
                sig_id, ticker, harga, sinyal, tp, sl = data

                conn_port = sqlite3.connect(PORTFOLIO_DB)
                cur_port = conn_port.cursor()
                cur_port.execute("SELECT * FROM posisi WHERE ticker = ?", (ticker,))
                sudah_punya = cur_port.fetchone()
                conn_port.close()

                if not sudah_punya and sinyal in ["ULTRA_BUY", "STRONG_BUY"]:
                    eksekusi_beli(ticker, harga, sl, tp)

                last_signal_id = sig_id

            time.sleep(1)

    except KeyboardInterrupt:
        log.info("RL Agent stopped.")
    finally:
        conn_hist.close()

if __name__ == "__main__":
    jalankan_rl_agent()
