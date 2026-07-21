# src/data/schema.py — Unified DB Schema (SKILL.md §②)
# ============================================================
# Single source of truth for ALL table definitions.
# Producer, Consumer AI, and Executor import from here.
# No duplicate CREATE TABLE statements anywhere else.

import sqlite3
import logging

logger = logging.getLogger(__name__)

# ── Market Data Tables (histori_ihsg.db) ──────────────────────────

SCALP_TABLES: dict[str, str] = {
    "histori_ihsg": """
        CREATE TABLE IF NOT EXISTS histori_ihsg (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker  TEXT    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            harga   REAL    NOT NULL CHECK(harga > 0),
            volume  REAL,
            waktu   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "sinyal_trading": """
        CREATE TABLE IF NOT EXISTS sinyal_trading (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker     TEXT    NOT NULL,
            harga      REAL,
            sinyal     TEXT,
            tp         REAL,
            sl         REAL,
            confidence REAL,
            waktu      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "log_error": """
        CREATE TABLE IF NOT EXISTS log_error (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            pesan  TEXT,
            waktu  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "consumer_state": """
        CREATE TABLE IF NOT EXISTS consumer_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """,
}

HISTORI_INDICES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_histori_ticker ON histori_ihsg(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_histori_waktu  ON histori_ihsg(waktu)",
    "CREATE INDEX IF NOT EXISTS idx_sinyal_ticker  ON sinyal_trading(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_sinyal_waktu   ON sinyal_trading(waktu)",
]

# ── Portfolio Tables (portofolio_virtual.db) ─────────────────────

PORTFOLIO_TABLES: dict[str, str] = {
    "akun": """
        CREATE TABLE IF NOT EXISTS akun (
            saldo_cash REAL
        )
    """,
    "posisi": """
        CREATE TABLE IF NOT EXISTS posisi (
            rowid         INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT,
            harga_beli    REAL,
            sl            REAL,
            tp            REAL,
            shares        INTEGER,
            tanggal       TEXT,
            highest_price REAL DEFAULT 0,
            strategy      TEXT DEFAULT 'scalp'
        )
    """,
    "histori_trade": """
        CREATE TABLE IF NOT EXISTS histori_trade (
            ticker   TEXT,
            pnl      REAL,
            status   TEXT,
            tanggal  TEXT,
            strategy TEXT DEFAULT 'scalp'
        )
    """,
    "state": """
        CREATE TABLE IF NOT EXISTS state (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """,
}


# ── Init Functions ───────────────────────────────────────────────

def init_histori_db(conn: sqlite3.Connection) -> None:
    """Initialize the market data database (histori_ihsg.db).

    Creates all tables and indices. Idempotent — safe to call
    multiple times from producer, consumer, or executor.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    for ddl in SCALP_TABLES.values():
        cur.execute(ddl)
    for idx in HISTORI_INDICES:
        cur.execute(idx)
    conn.commit()
    logger.info("histori_ihsg.db initialized (%d tables, %d indices)",
                len(SCALP_TABLES), len(HISTORI_INDICES))


def init_portfolio_db(conn: sqlite3.Connection, initial_capital: float) -> None:
    """Initialize the portfolio database (portofolio_virtual.db).

    Creates all tables. Seeds the akun table with initial_capital
    if no row exists. Idempotent.
    """
    cur = conn.cursor()
    for ddl in PORTFOLIO_TABLES.values():
        cur.execute(ddl)
    cur.execute("SELECT saldo_cash FROM akun")
    if not cur.fetchone():
        cur.execute("INSERT INTO akun (saldo_cash) VALUES (?)", (initial_capital,))
    conn.commit()
    logger.info("portofolio_virtual.db initialized (capital=Rp%,.0f)", initial_capital)


def get_or_create_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    """Read a state value. Returns default if key doesn't exist."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM state WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a state value."""
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
