"""
file_handler.py — Cache & File I/O for Screener
=================================================
Extracted from data_fetcher.py and screener.py during dekonstruksi phase.
Handles pickle cache, joblib cache, CSV export, ticker file loading.
"""

import os
import logging
import datetime

logger = logging.getLogger("file_handler")

# ─── Caching Setup ───────────────────────────────────────────────────────────
CACHE_DIR = "cache"

# Will be updated by caller on first use
USE_CACHE = True
CACHE_AVAILABLE = False

try:
    import joblib
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False

EXPORT_FILENAME_TEMPLATE = "screener_ihsg_{date}.csv"


def ensure_cache_dir():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)


# ── joblib-based cache (from screener.py) ──────────────────────────────
def get_cache_key(ticker, period, interval):
    """Generate unique cache filename."""
    return f"{ticker}_{period}_{interval}_{datetime.date.today().isoformat()}"


def load_from_cache(cache_key):
    """Load cached data using joblib."""
    if not CACHE_AVAILABLE or not USE_CACHE:
        return None
    ensure_cache_dir()
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.pkl")
    if os.path.exists(cache_file):
        try:
            return joblib.load(cache_file)
        except Exception:
            return None
    return None


def save_to_cache(cache_key, data):
    """Save data to joblib cache."""
    if not CACHE_AVAILABLE or not USE_CACHE:
        return
    ensure_cache_dir()
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.pkl")
    try:
        joblib.dump(data, cache_file)
    except Exception:
        pass


# ── Ticker file loading ────────────────────────────────────────────────
def normalize_ticker_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol:
        return ""
    return symbol if "." in symbol else f"{symbol}.JK"


def load_tickers_from_file(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    tickers = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            symbol = normalize_ticker_symbol(line)
            if symbol:
                tickers.append(symbol)
    return list(dict.fromkeys(tickers))
