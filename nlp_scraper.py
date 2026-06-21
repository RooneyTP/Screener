"""
nlp_scraper.py — NLP Sentiment Scraper (Enhanced)
===================================================
Phase-3 Fix #8:
  - Added SentimentIntensityAnalyzer from nltk.sentiment (VADER)
  - get_sentiment_compound(ticker) → float  compound score [-1, +1]
  - get_sentiment(ticker) → (score, label) backward-compatible tuple
  - Integrated VADER as primary scorer; keyword fallback if VADER unavailable
  - Added disk-based 30-minute cache to avoid repeated network calls

VADER vs keyword scoring
------------------------
VADER handles negation ("tidak naik"), intensifiers ("sangat laba"), and
punctuation heuristics that the simple keyword set could not capture.
The compound score is the single best summary value: +1 = most positive,
-1 = most negative, 0 = neutral.
"""

import hashlib
import json
import logging
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

logger = logging.getLogger("nlp_scraper")

# ── VADER availability ────────────────────────────────────────────────────────
try:
    from nltk.sentiment import SentimentIntensityAnalyzer as _VADER
    import nltk
    # Silently download vader_lexicon if not already present
    try:
        nltk.data.find("sentiment/vader_lexicon.zip")
    except LookupError:
        nltk.download("vader_lexicon", quiet=True)
    _vader_analyzer = _VADER()
    VADER_AVAILABLE = True
    logger.debug("VADER sentiment analyser loaded successfully.")
except Exception as _vader_err:
    VADER_AVAILABLE = False
    _vader_analyzer = None
    logger.warning("VADER not available (%s) — using keyword fallback.", _vader_err)

# ── Simple keyword fallback (Indonesian + English) ───────────────────────────
_KW_POSITIVE = frozenset({
    "profit", "laba", "naik", "dividen", "akuisisi", "growth", "bullish",
    "up", "gain", "record", "buy", "strong", "meroket", "cuan", "untung",
    "all-time-high", "ath", "meningkat", "positif", "surplus",
})
_KW_NEGATIVE = frozenset({
    "rugi", "anjlok", "turun", "gugatan", "pkpu", "suspensi", "loss",
    "down", "bearish", "sell", "weak", "default", "gagal", "minus",
    "jatuh", "koreksi", "negatif", "defisit", "bangkrut",
})

# ── Disk cache (30-minute TTL) ────────────────────────────────────────────────
_CACHE_DIR = ".sentiment_cache"
_CACHE_TTL  = 1800   # seconds

os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_key(ticker: str) -> str:
    return os.path.join(_CACHE_DIR, hashlib.md5(ticker.encode()).hexdigest() + ".json")


def _read_cache(ticker: str) -> Optional[dict]:
    path = _cache_key(ticker)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) < _CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _write_cache(ticker: str, payload: dict) -> None:
    path = _cache_key(ticker)
    try:
        payload["ts"] = time.time()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


# ── Core scoring helpers ──────────────────────────────────────────────────────
def _vader_score(text: str) -> float:
    """Return VADER compound score for text, or 0.0 on error."""
    if not VADER_AVAILABLE or not text:
        return 0.0
    try:
        return float(_vader_analyzer.polarity_scores(text)["compound"])
    except Exception:
        return 0.0


def _keyword_score(text: str) -> float:
    """Simple keyword-based fallback; returns value in [-1, +1]."""
    words = set(text.lower().split())
    pos   = len(words & _KW_POSITIVE)
    neg   = len(words & _KW_NEGATIVE)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def _score_title(text: str) -> float:
    """Score a news title using VADER if available, else keyword fallback."""
    return _vader_score(text) if VADER_AVAILABLE else _keyword_score(text)


# ── Public API ────────────────────────────────────────────────────────────────
def get_sentiment_compound(ticker: str) -> float:
    """
    Phase-3 NEW: Return a single compound sentiment score in [-1, +1].

    Uses VADER when available; keyword scoring otherwise.
    Results cached on disk for 30 minutes.

    Parameters
    ----------
    ticker : str  e.g. "BBCA.JK" or "BBCA"

    Returns
    -------
    compound : float in [-1, +1]
               > +0.05  → positive
               < -0.05  → negative
               else     → neutral
    """
    cached = _read_cache(ticker)
    if cached is not None:
        return float(cached.get("compound", 0.0))

    headlines = _fetch_rss_headlines(ticker)
    if not headlines:
        return 0.0

    scores = [_score_title(h) for h in headlines if h and len(h) > 5]
    if not scores:
        return 0.0

    import statistics
    compound = statistics.mean(scores)
    _write_cache(ticker, {"compound": compound, "n": len(scores)})
    logger.debug("[%s] sentiment compound=%.3f  n=%d", ticker, compound, len(scores))
    return float(compound)


def get_sentiment(ticker: str) -> tuple[float, str]:
    """
    Backward-compatible function used by screener.py.

    Returns
    -------
    (score, label) where label ∈ {"BULLISH", "BEARISH", "NEUTRAL"}
    """
    compound = get_sentiment_compound(ticker)

    if compound > 0.05:
        label = "BULLISH"
    elif compound < -0.05:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    return compound, label


def get_sentiment_score(ticker: str) -> float:
    """
    Alias used by screener.py Phase-3 sentiment boost integration.
    Returns compound score directly.
    """
    return get_sentiment_compound(ticker)


def fetch_yahoo_finance_news(ticker: str, use_cache: bool = True) -> list[dict]:
    """
    Fetch recent news headlines from Yahoo Finance RSS for a ticker.

    Returns
    -------
    list of dicts with key 'title' (and optionally 'link')
    """
    if use_cache:
        cached = _read_cache(ticker + "_raw")
        if cached is not None:
            return cached.get("articles", [])

    clean = ticker.replace(".JK", "")
    url   = f"https://finance.yahoo.com/rss/headline?s={clean}"
    articles: list[dict] = []

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            root = ET.fromstring(resp.read())
        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el  = item.find("link")
            if title_el is not None and title_el.text:
                articles.append({
                    "title": title_el.text.strip(),
                    "link":  link_el.text.strip() if link_el is not None else "",
                })
    except Exception as exc:
        logger.debug("[%s] Yahoo RSS fetch failed: %s", ticker, exc)

    if use_cache and articles:
        _write_cache(ticker + "_raw", {"articles": articles})

    return articles


def _fetch_rss_headlines(ticker: str) -> list[str]:
    """
    Aggregate headlines from multiple RSS sources.

    Sources: Yahoo Finance + CNBC Indonesia
    Returns a flat list of title strings.
    """
    titles: list[str] = []

    # 1. Yahoo Finance RSS
    yf_articles = fetch_yahoo_finance_news(ticker, use_cache=True)
    titles.extend(a.get("title", "") for a in yf_articles[:10])

    # 2. CNBC Indonesia RSS (keyword match)
    try:
        url = "https://www.cnbcindonesia.com/market/rss"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        clean = ticker.replace(".JK", "").upper()
        with urllib.request.urlopen(req, timeout=3) as resp:
            root = ET.fromstring(resp.read())
        for item in root.findall(".//item/title"):
            text = (item.text or "").upper()
            if clean in text:
                titles.append(item.text or "")
    except Exception as exc:
        logger.debug("[%s] CNBC RSS fetch failed: %s", ticker, exc)

    return titles