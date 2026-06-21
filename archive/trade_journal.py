"""
trade_journal.py — Trade Journal untuk mencatat semua transaksi harian.
"""

import sqlite3
import json
import os
from datetime import datetime

JOURNAL_DB = "trade_journal.db"


def _get_conn():
    conn = sqlite3.connect(JOURNAL_DB)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            quantity INTEGER NOT NULL,
            entry_dt TEXT NOT NULL,
            exit_dt TEXT,
            pnl_rp REAL,
            pnl_pct REAL,
            strategy TEXT DEFAULT 'scalping',
            bot_name TEXT DEFAULT 'scalping_bot',
            tag TEXT DEFAULT 'INTRADAY',
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS strategy_performance (
            strategy TEXT PRIMARY KEY,
            total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS daily_totals (
            date TEXT PRIMARY KEY,
            trades INTEGER DEFAULT 0,
            pnl REAL DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            vol_rp REAL DEFAULT 0
        );
    """)
    return conn


def log_entry(ticker, direction, entry_price, quantity,
              entry_dt=None, strategy="scalping", bot_name="scalping_bot",
              tag="INTRADAY"):
    """Log a trade entry."""
    if entry_dt is None:
        entry_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    conn = _get_conn()
    conn.execute(
        "INSERT INTO trades (ticker, direction, entry_price, quantity, entry_dt, strategy, bot_name, tag) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker, direction, entry_price, quantity, entry_dt, strategy, bot_name, tag)
    )
    conn.commit()
    conn.close()


def log_exit(ticker, exit_price, pnl_rp, pnl_pct,
             exit_dt=None, notes=""):
    """Log a trade exit (update the last open entry for this ticker)."""
    if exit_dt is None:
        exit_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    conn = _get_conn()
    # Find the most recent open trade for this ticker
    cursor = conn.execute(
        "SELECT id, strategy FROM trades WHERE ticker = ? AND exit_price IS NULL ORDER BY id DESC LIMIT 1",
        (ticker,)
    )
    row = cursor.fetchone()
    if row:
        trade_id, strategy = row
        conn.execute(
            "UPDATE trades SET exit_price = ?, exit_dt = ?, pnl_rp = ?, pnl_pct = ?, notes = ? WHERE id = ?",
            (exit_price, exit_dt, pnl_rp, pnl_pct, notes, trade_id)
        )
        # Update strategy performance table
        is_win = 1 if pnl_rp > 0 else 0
        conn.execute(
            "INSERT INTO strategy_performance (strategy, total_trades, wins, total_pnl) "
            "VALUES (?, 1, ?, ?) "
            "ON CONFLICT(strategy) DO UPDATE SET "
            "total_trades = total_trades + 1, "
            "wins = wins + ?, "
            "total_pnl = total_pnl + ?",
            (strategy, is_win, pnl_rp, is_win, pnl_rp)
        )
        # Update daily totals
        today = datetime.now().strftime("%Y-%m-%d")
        is_loss = 1 if pnl_rp < 0 else 0
        vol_rp = abs(pnl_rp)
        conn.execute(
            "INSERT INTO daily_totals (date, trades, pnl, wins, losses, vol_rp) "
            "VALUES (?, 1, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "trades = trades + 1, "
            "pnl = pnl + ?, "
            "wins = wins + ?, "
            "losses = losses + ?, "
            "vol_rp = vol_rp + ?",
            (today, pnl_rp, is_win, is_loss, vol_rp, pnl_rp, is_win, is_loss, vol_rp)
        )
    conn.commit()
    conn.close()


def get_daily_summary(date_str=None):
    """Get daily performance summary."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    conn = _get_conn()
    cursor = conn.execute("SELECT * FROM daily_totals WHERE date = ?", (date_str,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "date": row[0],
            "trades": row[1],
            "pnl": row[2],
            "wins": row[3],
            "losses": row[4],
            "vol_rp": row[5]
        }
    return None


def get_open_positions():
    """Get all open (unclosed) positions."""
    conn = _get_conn()
    cursor = conn.execute(
        "SELECT ticker, direction, entry_price, quantity, entry_dt, strategy, tag "
        "FROM trades WHERE exit_price IS NULL ORDER BY entry_dt DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "ticker": r[0], "direction": r[1], "entry_price": r[2],
            "quantity": r[3], "entry_dt": r[4], "strategy": r[5],
            "tag": r[6]
        }
        for r in rows
    ]
