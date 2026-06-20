#!/usr/bin/env python3
"""
chat_memory.py — SQLite Conversation Memory untuk @QuantYan_bot
Menyimpan riwayat chat per user_id + chat_id, inject sebagai context ke LLM.

Fitur:
  - Simpan setiap pesan (user & assistant) dengan timestamp
  - Track context_ticker & context_sector terakhir per user
  - Ambil N pesan terakhir untuk di-inject ke LLM history
  - Deteksi apakah user baru (belum pernah chat) untuk onboarding
  - Auto-cleanup pesan lama (>7 hari) untuk hemat ruang
  - Thread-safe (pakai connection per call, bukan singleton)
"""
import os
import sqlite3
import time
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("chat_memory")

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "chat_memory.db")

# ── Schema ────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,          -- 'user' | 'assistant'
    content TEXT NOT NULL,
    context_ticker TEXT,         -- ticker terakhir yang dibahas
    context_sector TEXT,         -- sektor terakhir yang dibahas
    ts REAL NOT NULL             -- Unix timestamp
);

CREATE INDEX IF NOT EXISTS idx_messages_user_chat
    ON messages(user_id, chat_id, ts DESC);

CREATE TABLE IF NOT EXISTS user_context (
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    last_ticker TEXT,
    last_sector TEXT,
    last_mode TEXT,              -- 'swing' | 'scalp' | NULL
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, chat_id)
);
"""

_initialized = False


def _get_conn() -> sqlite3.Connection:
    """Buat koneksi baru setiap kali (thread-safe pattern untuk SQLite)."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema():
    """Buat tabel kalau belum ada (dipanggil sekali)."""
    global _initialized
    if _initialized:
        return
    conn = _get_conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        _initialized = True
    finally:
        conn.close()


# ── Public API ────────────────────────────────────────────────────

def save_message(
    user_id: int,
    chat_id: int,
    role: str,
    content: str,
    context_ticker: Optional[str] = None,
    context_sector: Optional[str] = None,
):
    """Simpan 1 pesan ke DB + update user_context."""
    _ensure_schema()
    now = time.time()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO messages (user_id, chat_id, role, content, context_ticker, context_sector, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, role, content[:4000], context_ticker, context_sector, now),
        )
        # Upsert user_context
        conn.execute(
            """INSERT INTO user_context (user_id, chat_id, last_ticker, last_sector, last_mode, first_seen, last_seen, message_count)
               VALUES (?, ?, ?, ?, NULL, ?, ?, 1)
               ON CONFLICT(user_id, chat_id) DO UPDATE SET
                   last_ticker = COALESCE(excluded.last_ticker, user_context.last_ticker),
                   last_sector = COALESCE(excluded.last_sector, user_context.last_sector),
                   last_seen = excluded.last_seen,
                   message_count = user_context.message_count + 1""",
            (user_id, chat_id, context_ticker, context_sector, now, now),
        )
        conn.commit()
    except Exception as e:
        logger.warning("save_message error: %s", e)
    finally:
        conn.close()


def get_recent_messages(
    user_id: int,
    chat_id: int,
    limit: int = 5,
) -> List[Dict[str, str]]:
    """Ambil N pesan terakhir (user+assistant) untuk inject ke LLM history."""
    _ensure_schema()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE user_id=? AND chat_id=? "
            "ORDER BY ts DESC LIMIT ?",
            (user_id, chat_id, limit),
        ).fetchall()
        # Balik urutan supaya kronologis (oldest first)
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    finally:
        conn.close()


def get_user_context(user_id: int, chat_id: int) -> Dict:
    """Ambil last_ticker, last_sector, last_mode, message_count."""
    _ensure_schema()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT last_ticker, last_sector, last_mode, message_count, first_seen "
            "FROM user_context WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        ).fetchone()
        if row:
            return {
                "last_ticker": row["last_ticker"],
                "last_sector": row["last_sector"],
                "last_mode": row["last_mode"],
                "message_count": row["message_count"],
                "first_seen": row["first_seen"],
                "is_new_user": False,
            }
        return {
            "last_ticker": None,
            "last_sector": None,
            "last_mode": None,
            "message_count": 0,
            "first_seen": None,
            "is_new_user": True,
        }
    finally:
        conn.close()


def update_context(
    user_id: int,
    chat_id: int,
    ticker: Optional[str] = None,
    sector: Optional[str] = None,
    mode: Optional[str] = None,
):
    """Update last_ticker / last_sector / last_mode."""
    _ensure_schema()
    conn = _get_conn()
    try:
        parts = []
        vals = []
        if ticker is not None:
            parts.append("last_ticker=?")
            vals.append(ticker.upper())
        if sector is not None:
            parts.append("last_sector=?")
            vals.append(sector)
        if mode is not None:
            parts.append("last_mode=?")
            vals.append(mode)
        if not parts:
            return
        vals.extend([user_id, chat_id])
        conn.execute(
            f"UPDATE user_context SET {', '.join(parts)} WHERE user_id=? AND chat_id=?",
            vals,
        )
        conn.commit()
    except Exception as e:
        logger.warning("update_context error: %s", e)
    finally:
        conn.close()


def is_new_user(user_id: int, chat_id: int) -> bool:
    """Cek apakah user_id belum pernah chat di chat_id ini."""
    ctx = get_user_context(user_id, chat_id)
    return ctx["is_new_user"]


def cleanup_old_messages(max_age_days: int = 7):
    """Hapus pesan lebih tua dari max_age_days. Dipanggil periodik."""
    _ensure_schema()
    cutoff = time.time() - (max_age_days * 86400)
    conn = _get_conn()
    try:
        result = conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
        conn.commit()
        deleted = result.rowcount
        if deleted > 0:
            logger.info("Cleaned up %d old messages (>%d days)", deleted, max_age_days)
    finally:
        conn.close()


# ── Self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing chat_memory...")
    save_message(1, 100, "user", "Cek BBCA dong", context_ticker="BBCA")
    save_message(1, 100, "assistant", "BBCA harganya Rp9.850...")
    save_message(1, 100, "user", "Fundamentalnya gimana?")
    msgs = get_recent_messages(1, 100, limit=5)
    print(f"Recent messages ({len(msgs)}):")
    for m in msgs:
        print(f"  [{m['role']}] {m['content'][:60]}")
    ctx = get_user_context(1, 100)
    print(f"Context: {ctx}")
    print(f"is_new_user(999, 100) = {is_new_user(999, 100)}")
    print("OK!")
