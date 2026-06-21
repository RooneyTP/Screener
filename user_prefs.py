#!/usr/bin/env python3
"""
user_prefs.py — Persistensi preferensi user lintas sesi.

Menyimpan preferensi tiap user+chat_id ke file JSON sehingga bot bisa
mengingat risk tolerance, mode favorit, ticker favorit, dan depth-level
dari sesi ke sesi — layaknya ChatGPT yang kenal penggunanya.
"""
import os
import json
import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("user_prefs")

ROOT = os.path.dirname(os.path.abspath(__file__))
PREFS_DIR = os.path.join(ROOT, "data")
PREFS_PATH = os.path.join(PREFS_DIR, "user_prefs.json")

_lock = None  # Simple file-level lock via temp atomic write

# ── Const defaults ────────────────────────────────────────────────
DEFAULT_PREFS = {
    "mode": "swing",               # swing | scalp | invest | entry
    "risk_tolerance": "moderate",  # conservative | moderate | aggressive
    "depth_mode": "normal",        # light | normal | deep
    "favorite_tickers": [],         # list of str
    "preferred_sectors": [],        # list of str
    "total_conversations": 0,
    "last_depth_mode": "normal",
    "first_seen": None,
    "last_seen": None,
}


def _ensure_dir():
    os.makedirs(PREFS_DIR, exist_ok=True)


def _read_all() -> Dict[str, Any]:
    _ensure_dir()
    if not os.path.exists(PREFS_PATH):
        return {}
    try:
        with open(PREFS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Gagal baca user_prefs.json: %s", e)
        return {}


def _write_all(data: Dict[str, Any]):
    _ensure_dir()
    tmp = PREFS_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, PREFS_PATH)  # atomic on same filesystem
    except OSError as e:
        logger.warning("Gagal write user_prefs.json: %s", e)


def _key(user_id: int, chat_id: int) -> str:
    return f"{user_id}:{chat_id}"


def load_prefs(user_id: int, chat_id: int) -> Dict[str, Any]:
    """Ambil preferensi user. Kembalikan default jika belum ada."""
    data = _read_all()
    prefs = data.get(_key(user_id, chat_id), {})
    out = dict(DEFAULT_PREFS)
    out.update(prefs)
    return out


def save_prefs(user_id: int, chat_id: int, prefs: Dict[str, Any]):
    """Simpan preferensi user secara lengkap (merge dengan default)."""
    data = _read_all()
    merged = dict(DEFAULT_PREFS)
    existing = data.get(_key(user_id, chat_id), {})
    merged.update(existing)
    merged.update(prefs)
    now = time.time()
    if merged["first_seen"] is None:
        merged["first_seen"] = now
    merged["last_seen"] = now
    data[_key(user_id, chat_id)] = merged
    _write_all(data)


def update_pref(user_id: int, chat_id: int, key: str, value: Any):
    """Update satu key preferensi user."""
    prefs = load_prefs(user_id, chat_id)
    prefs[key] = value
    save_prefs(user_id, chat_id, prefs)


def track_ticker_interest(user_id: int, chat_id: int, ticker: str, sector: Optional[str] = None):
    """Catat ketertarikan user terhadap ticker tertentu."""
    prefs = load_prefs(user_id, chat_id)
    # Tambah ke favorite_tickers (maks 10, paling baru di depan)
    ticker = ticker.upper()
    favs = [t for t in prefs.get("favorite_tickers", []) if t != ticker]
    favs.insert(0, ticker)
    prefs["favorite_tickers"] = favs[:10]
    if sector:
        secs = [s for s in prefs.get("preferred_sectors", []) if s.lower() != sector.lower()]
        secs.insert(0, sector.title())
        prefs["preferred_sectors"] = secs[:5]
    prefs["total_conversations"] = prefs.get("total_conversations", 0) + 1
    save_prefs(user_id, chat_id, prefs)


def get_ticker_prefs(user_id: int, chat_id: int) -> Dict[str, Any]:
    """Ambil data ringkas preferensi user yang berguna untuk LLM context."""
    p = load_prefs(user_id, chat_id)
    return {
        "mode": p.get("mode", "swing"),
        "risk": p.get("risk_tolerance", "moderate"),
        "depth": p.get("depth_mode", "normal"),
        "fav_tickers": p.get("favorite_tickers", [])[:5],
        "fav_sectors": p.get("preferred_sectors", [])[:3],
        "total_conversations": p.get("total_conversations", 0),
    }


# ── Self-Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    uid, cid = 12345, -999
    print("Save prefs...")
    save_prefs(uid, cid, {"mode": "swing", "risk_tolerance": "moderate"})
    print("Load prefs:", load_prefs(uid, cid))
    print("Track ticker BBCA...")
    track_ticker_interest(uid, cid, "BBCA", "Bank")
    print("Track ticker BBRI...")
    track_ticker_interest(uid, cid, "BBRI", "Bank")
    print("Ticker prefs:", get_ticker_prefs(uid, cid))
    print("Update depth to deep...")
    update_pref(uid, cid, "depth_mode", "deep")
    print("Final:", get_ticker_prefs(uid, cid))
    print("=== USER_PREFS OK ===")
