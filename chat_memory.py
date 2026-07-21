#!/usr/bin/env python3
"""
chat_memory.py — SQLite Conversation Memory untuk @QuantYan_bot
Menyimpan riwayat chat per user_id + chat_id, inject context ke LLM.

v2 — Fix Memory Lupa:
  - Inject 10 pesan terakhir (dari 5)
  - Summarization otomatis tiap 12 turn — preserve inti obrolan
  - Web search context: simpan hasil riset terakhir
  - Conversation topics: track topik utama diskusi
  - Thread-safe (connection per call)
"""
import os
import sqlite3
import time
import logging
from typing import List, Dict, Optional, Any

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
    context_ticker TEXT,
    context_sector TEXT,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_user_chat
    ON messages(user_id, chat_id, ts DESC);

CREATE TABLE IF NOT EXISTS user_context (
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    last_ticker TEXT,
    last_sector TEXT,
    last_mode TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    message_count INTEGER DEFAULT 0,
    conversation_summary TEXT DEFAULT '',
    web_search_cache TEXT DEFAULT '',
    topics TEXT DEFAULT '',
    PRIMARY KEY (user_id, chat_id)
);
"""

_initialized = False


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema():
    global _initialized
    if _initialized:
        return
    conn = _get_conn()
    try:
        conn.executescript(_SCHEMA)
        # Cek kolom tambahan (v2 upgrade)
        cursor = conn.execute("PRAGMA table_info(user_context)")
        cols = {r["name"] for r in cursor.fetchall()}
        upgrades = []
        if "conversation_summary" not in cols:
            upgrades.append("ALTER TABLE user_context ADD COLUMN conversation_summary TEXT DEFAULT ''")
        if "web_search_cache" not in cols:
            upgrades.append("ALTER TABLE user_context ADD COLUMN web_search_cache TEXT DEFAULT ''")
        if "topics" not in cols:
            upgrades.append("ALTER TABLE user_context ADD COLUMN topics TEXT DEFAULT ''")
        for u in upgrades:
            conn.execute(u)
        conn.commit()
        _initialized = True
    finally:
        conn.close()


# ── Core API ───────────────────────────────────────────────────────

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
        conn.execute(
            """INSERT INTO user_context (user_id, chat_id, last_ticker, last_sector, last_mode,
               first_seen, last_seen, message_count)
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
    limit: int = 10,
) -> List[Dict[str, str]]:
    """Ambil N pesan terakhir (user+assistant) untuk inject ke LLM."""
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
    """Ambil last_ticker, last_sector, last_mode, message_count, summary, topics."""
    _ensure_schema()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT last_ticker, last_sector, last_mode, message_count, first_seen, "
            "conversation_summary, topics "
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
                "conversation_summary": row["conversation_summary"] or "",
                "topics": row["topics"] or "",
                "is_new_user": False,
            }
        return {
            "last_ticker": None, "last_sector": None, "last_mode": None,
            "message_count": 0, "first_seen": None,
            "conversation_summary": "", "topics": "",
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


def update_conversation_summary(user_id: int, chat_id: int, summary: str):
    """Simpan ringkasan percakapan ke DB."""
    _ensure_schema()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE user_context SET conversation_summary=? WHERE user_id=? AND chat_id=?",
            (summary[:2000], user_id, chat_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning("update_summary error: %s", e)
    finally:
        conn.close()


def update_topics(user_id: int, chat_id: int, topics: str):
    """Simpan topik-topik yang dibahas."""
    _ensure_schema()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE user_context SET topics=? WHERE user_id=? AND chat_id=?",
            (topics[:500], user_id, chat_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning("update_topics error: %s", e)
    finally:
        conn.close()


def save_web_search_cache(user_id: int, chat_id: int, search_result: str):
    """Simpan hasil riset web terakhir untuk referensi."""
    _ensure_schema()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE user_context SET web_search_cache=? WHERE user_id=? AND chat_id=?",
            (search_result[:2000], user_id, chat_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning("save_web_search error: %s", e)
    finally:
        conn.close()


def get_web_search_cache(user_id: int, chat_id: int) -> str:
    """Ambil hasil riset web terakhir."""
    _ensure_schema()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT web_search_cache FROM user_context WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        ).fetchone()
        return row["web_search_cache"] if row and row["web_search_cache"] else ""
    finally:
        conn.close()


def get_conversation_for_inject(user_id: int, chat_id: int) -> Dict[str, Any]:
    """
    Ambil semua konteks percakapan untuk di-inject ke system prompt LLM.
    """
    ctx = get_user_context(user_id, chat_id)
    recent = get_recent_messages(user_id, chat_id, limit=10)
    stats = get_user_recent_context_stats(user_id, chat_id, limit=10)

    return {
        "context": ctx,
        "recent_messages": recent,
        "stats": stats,
    }


def is_new_user(user_id: int, chat_id: int) -> bool:
    ctx = get_user_context(user_id, chat_id)
    return ctx["is_new_user"]


def get_user_recent_context_stats(user_id: int, chat_id: int, limit: int = 10) -> Dict[str, list]:
    """Ambil statistik ticker & sektor dari riwayat."""
    _ensure_schema()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT context_ticker, context_sector FROM messages "
            "WHERE user_id=? AND chat_id=? AND role='user' "
            "ORDER BY ts DESC LIMIT ?",
            (user_id, chat_id, limit)
        ).fetchall()
        tickers = [r["context_ticker"] for r in rows if r["context_ticker"]]
        sectors = [r["context_sector"] for r in rows if r["context_sector"]]
        return {"recent_tickers": tickers, "recent_sectors": sectors}
    finally:
        conn.close()


def cleanup_old_messages(max_age_days: int = 7):
    """Hapus pesan lebih tua dari max_age_days."""
    _ensure_schema()
    cutoff = time.time() - (max_age_days * 86400)
    conn = _get_conn()
    try:
        result = conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
        conn.commit()
        if result.rowcount > 0:
            logger.info("Cleaned up %d old messages (>%d days)", result.rowcount, max_age_days)
    finally:
        conn.close()


# ── Self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing chat_memory v2...")
    save_message(1, 100, "user", "Cek BBCA dong", context_ticker="BBCA")
    save_message(1, 100, "assistant", "BBCA harganya Rp9.850...")
    save_message(1, 100, "user", "Fundamentalnya gimana?")
    ctx = get_conversation_for_inject(1, 100)
    print(f"Recent messages: {len(ctx['recent_messages'])}")
    print(f"Context: ticker={ctx['context'].get('last_ticker')}, new={ctx['context'].get('is_new_user')}")
    update_conversation_summary(1, 100, "Diskusi tentang BBCA, user minta cek fundamental")
    update_topics(1, 100, "BBCA, fundamental, saham bank")
    save_web_search_cache(1, 100, "BBCA: harga Rp9.850, PE 18, PBV 2.5")
    ctx2 = get_conversation_for_inject(1, 100)
    print(f"Summary: {ctx2['context'].get('conversation_summary')}")
    print(f"Web cache: {get_web_search_cache(1, 100)[:50]}...")
    print("OK!")
