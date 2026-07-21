"""
scraper.py — Data Fetching & Scraping for Screener
====================================================
Merged from data_fetcher.py + scraping functions from screener.py.
Handles all external data sources: Yahoo Finance, news, foreign flow, etc.
"""

import os
import time
import logging
import datetime
import pandas as pd
import numpy as np

logger = logging.getLogger("scraper")

# Will be imported lazily to avoid circular deps at module level
yf = None


def _ensure_yfinance():
    global yf
    if yf is None:
        try:
            import yfinance as _yf
            yf = _yf
        except ImportError:
            import subprocess
            subprocess.run(["pip", "install", "yfinance", "-q"], check=True)
            import yfinance as _yf
            yf = _yf


# ── Macro cache (lazy loaded) ───────────────────────────────────────────
_macro_data_cache = None


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL MACRO VARIABLES
# ═══════════════════════════════════════════════════════════════════════════

# Define defaults - will be updated when needed
IHSG_CHANGE = 0.0
SP500_CHANGE = 0.0
USD_CHANGE = 0.0
BRENT_CHANGE = 0.0
GOLD_CHANGE = 0.0
COAL_CHANGE = 0.0
USD_PRICE = 16000.0
ihsg_data = pd.Series()
MACRO_PENALTY = 0.0
IHSG_TREND = "UP"


# ═══════════════════════════════════════════════════════════════════════════
# DATA FETCHING (from data_fetcher.py)
# ═══════════════════════════════════════════════════════════════════════════

# Import cache functions from file_handler
from core.file_handler import get_cache_key, load_from_cache, save_to_cache


def fetch_price_data_sync(ticker: str, period: str = "6mo", interval: str = "1d",
                          skip_cache: bool = False) -> pd.DataFrame:
    """Mengambil data harga saham menggunakan Yahoo Finance."""
    _ensure_yfinance()
    cache_key = get_cache_key(ticker, period, interval)

    # Cek cache dulu (kecuali jika skip_cache=True)
    if not skip_cache:
        data = load_from_cache(cache_key)
        if data is not None and not data.empty:
            return data

    try:
        # Tambahkan .JK jika belum ada (untuk saham Indonesia)
        if not ticker.endswith(".JK") and len(ticker) <= 4:
            ticker = f"{ticker.upper()}.JK"

        time.sleep(0.1)
        tkr = yf.Ticker(ticker)
        data = tkr.history(period=period, interval=interval)

        if data.empty:
            print(f"\u26a0\ufe0f [YFinance] Data {ticker} tidak ditemukan.")
            return pd.DataFrame()

        # Pembersihan Data
        data.index = pd.to_datetime(data.index).tz_localize(None)

        # Pastikan kolom standar tersedia
        if 'Volume' not in data.columns:
            data['Volume'] = 0

        data = data[['Open', 'High', 'Low', 'Close', 'Volume']]

        # Simpan hasil sukses ke cache
        save_to_cache(cache_key, data)
        print(f"[YFinance] Berhasil tarik {ticker} ({len(data)} hari)")
        return data

    except Exception as e:
        print(f"[YFinance] Error saat mengambil {ticker}: {e}")
        return pd.DataFrame()


def fetch_multiple_tickers_sync(tickers: list, period: str = "6mo", interval: str = "1d") -> dict:
    """Mengambil banyak saham sekaligus secara berurutan."""
    results = {}
    for ticker in tickers:
        results[ticker] = fetch_price_data_sync(ticker, period, interval)
    return results


def fetch_macro_data() -> dict:
    """Mengambil data makro ekonomi global (IHSG, S&P500, USD, Komoditas)."""
    _ensure_yfinance()
    try:
        macro_list = ["^JKSE", "^GSPC", "IDR=X", "BZ=F", "GC=F", "MTF=F"]
        macro_data = yf.download(macro_list, period="2mo", progress=False)['Close']

        jkse_clean = macro_data["^JKSE"].dropna() if "^JKSE" in macro_data.columns else pd.Series()
        gspc_clean = macro_data["^GSPC"].dropna() if "^GSPC" in macro_data.columns else pd.Series()
        idr_clean = macro_data["IDR=X"].dropna() if "IDR=X" in macro_data.columns else pd.Series()
        brent_clean = macro_data["BZ=F"].dropna() if "BZ=F" in macro_data.columns else pd.Series()
        gold_clean = macro_data["GC=F"].dropna() if "GC=F" in macro_data.columns else pd.Series()
        coal_clean = macro_data["MTF=F"].dropna() if "MTF=F" in macro_data.columns else pd.Series()

        def get_change(series):
            if len(series) >= 2:
                return float((series.iloc[-1] - series.iloc[-2]) / series.iloc[-2] * 100)
            return 0.0

        return {
            "ihsg_change": get_change(jkse_clean),
            "sp500_change": get_change(gspc_clean),
            "usd_change": get_change(idr_clean),
            "brent_change": get_change(brent_clean),
            "gold_change": get_change(gold_clean),
            "coal_change": get_change(coal_clean),
            "usd_price": float(idr_clean.iloc[-1]) if not idr_clean.empty else 16000.0,
            "ihsg_data": jkse_clean
        }
    except Exception as e:
        logger.warning("Gagal mengambil data makro: %s", e)
        return {
            "ihsg_change": 0.0, "sp500_change": 0.0, "usd_change": 0.0,
            "brent_change": 0.0, "gold_change": 0.0, "coal_change": 0.0,
            "usd_price": 16000.0, "ihsg_data": pd.Series()
        }


# ═══════════════════════════════════════════════════════════════════════════
# MACRO UPDATE FUNCTIONS (from screener.py)
# ═══════════════════════════════════════════════════════════════════════════

def get_macro_data():
    """Lazy load macro data on first use."""
    global _macro_data_cache
    if _macro_data_cache is None:
        logger.info("Mengunduh Cuaca Makro Global & Komoditas...")
        _macro_data_cache = fetch_macro_data()
    return _macro_data_cache


import threading
_counter_lock = threading.Lock()


def update_macro_globals():
    """Ambil data macro dan update semua global. Panggil sekali di awal screener."""
    global IHSG_CHANGE, SP500_CHANGE, USD_CHANGE, BRENT_CHANGE
    global GOLD_CHANGE, COAL_CHANGE, USD_PRICE, ihsg_data
    global MACRO_PENALTY, IHSG_TREND
    try:
        macro = get_macro_data()
        if not macro:
            return
        IHSG_CHANGE  = float(macro.get("ihsg_change",   0.0))
        SP500_CHANGE = float(macro.get("sp500_change",  0.0))
        USD_CHANGE   = float(macro.get("usd_change",    0.0))
        BRENT_CHANGE = float(macro.get("brent_change",  0.0))
        GOLD_CHANGE  = float(macro.get("gold_change",   0.0))
        COAL_CHANGE  = float(macro.get("coal_change",   0.0))
        USD_PRICE    = float(macro.get("usd_price",  16000.0))
        ihsg_series  = macro.get("ihsg_data", None)
        if ihsg_series is not None:
            global ihsg_data
            ihsg_data = pd.Series(ihsg_series) if not isinstance(ihsg_series, pd.Series) else ihsg_series

        # Hitung MACRO_PENALTY dari kondisi riil
        penalty = 0.0
        if IHSG_CHANGE  < -1.5: penalty -= 1.0
        if SP500_CHANGE < -1.0: penalty -= 0.5
        if USD_CHANGE   >  0.5: penalty -= 0.5
        if BRENT_CHANGE >  3.0: penalty -= 0.5
        if IHSG_CHANGE  >  1.0: penalty += 0.5
        if GOLD_CHANGE  >  1.0: penalty -= 0.5
        global MACRO_PENALTY
        MACRO_PENALTY = round(penalty, 1)

        # Update IHSG_TREND
        if len(ihsg_data) >= 3:
            last3 = ihsg_data.tail(3)
            global IHSG_TREND
            IHSG_TREND = "UP" if (last3.diff().dropna() > 0).all() else "DOWN"
        else:
            IHSG_TREND = "UP" if IHSG_CHANGE > 0 else "DOWN"

        logger.info(
            "Macro update -- IHSG=%+.2f%% | USD=%+.2f%% | Trend=%s | Penalty=%.1f",
            IHSG_CHANGE, USD_CHANGE, IHSG_TREND, MACRO_PENALTY
        )
    except Exception as e:
        logger.error("update_macro_globals() gagal: %s", e, exc_info=True)


# ═══════════════════════════════════════════════════════════════════════════
# SCRAPING FUNCTIONS (from screener.py)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_foreign_flow(ticker: str) -> dict:
    """[v10.0] Real foreign flow via RTI Business scraper."""
    try:
        return fetch_foreign_flow_real(ticker)
    except Exception:
        return {"net_foreign_5d": 0.0, "net_foreign_pct": 0.0, "foreign_status": "NEUTRAL", "source": "fallback"}


def fetch_berita_lokal(ticker: str) -> int:
    """Scraper senyap menggunakan RSS Feed portal berita Indonesia (CNBC/Kontan)"""
    import urllib.request
    import xml.etree.ElementTree as ET
    score = 0
    try:
        url = "https://www.cnbcindonesia.com/market/rss"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)

            for item in root.findall('.//item/title'):
                title = (item.text or "").upper()
                if ticker.replace(".JK", "") in title:
                    if any(word in title for word in ["CUM CUAN", "LABA", "MEROKET", "AKUISISI", "DIVIDEN"]):
                        score += 1
                    elif any(word in title for word in ["RUGI", "ANJLOK", "GUGATAN", "PKPU", "SUSPENSI"]):
                        score -= 1
    except Exception:
        pass
    return score


def fetch_fundamental_metrics(ticker: str) -> dict[str, float | int | bool | str]:
    """Fetch fundamental data from Yahoo Finance."""
    from utils.helpers import safe_float, safe_int

    _ensure_yfinance()

    result = {
        "float_shares": 0,
        "shares_outstanding": 0,
        "market_cap": 0,
        "trailing_pe": 0,
        "book_value": 0,
        "current_price": 0.0,
        "float_estimated": False,
        "float_source": "reported"
    }

    try:
        ticker_obj = yf.Ticker(ticker)

        info = {}
        fast_info = {}

        if hasattr(ticker_obj, "info"):
            info = ticker_obj.info or {}
        if not info and hasattr(ticker_obj, "get_info"):
            info = ticker_obj.get_info() or {}

        if hasattr(ticker_obj, "fast_info"):
            fast_info = ticker_obj.fast_info or {}

        result["current_price"] = safe_float(
            info.get("currentPrice") or
            info.get("regularMarketPrice") or
            fast_info.get("last_price") or
            fast_info.get("previousClose") or 0
        )

        result["shares_outstanding"] = safe_int(
            info.get("sharesOutstanding") or
            info.get("shares_outstanding") or
            fast_info.get("sharesOutstanding") or
            fast_info.get("shares") or 0
        )

        result["float_shares"] = safe_int(
            info.get("floatShares") or
            info.get("sharesFloat") or
            info.get("publicFloat") or
            info.get("shareFloat") or
            info.get("float_shares") or 0
        )

        if result["float_shares"] <= 0 and result["shares_outstanding"] > 0:
            result["float_shares"] = int(result["shares_outstanding"] * 0.25)
            result["float_estimated"] = True
            result["float_source"] = "proxy"

        if result["market_cap"] <= 0:
            result["market_cap"] = safe_int(
                info.get("marketCap") or
                fast_info.get("market_cap") or
                (result["current_price"] * result["shares_outstanding"] if result["current_price"] > 0 else 0)
            )

        result["trailing_pe"] = safe_float(info.get("trailingPE") or info.get("trailing_pe") or 0)
        result["book_value"] = safe_float(info.get("bookValue") or 0)

    except Exception:
        pass

    return result


def fetch_news_sentiment(ticker: str) -> dict:
    """Fetch news sentiment for a ticker using VADER or keyword fallback."""
    sentiment_result = {
        "sentiment_score": 0.0,
        "sentiment_label": "NEUTRAL",
        "news_count": 0,
        "positive_news": 0,
        "negative_news": 0
    }

    try:
        from nlp_scraper import fetch_yahoo_finance_news
        news = fetch_yahoo_finance_news(ticker, use_cache=True)

        if not news:
            return sentiment_result

        SENTIMENT_AVAILABLE = False
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            SENTIMENT_AVAILABLE = True
        except ImportError:
            pass

        if SENTIMENT_AVAILABLE:
            analyzer = SentimentIntensityAnalyzer()

            def _score_title(text: str) -> float:
                return analyzer.polarity_scores(text)["compound"]
        else:
            _POSITIVE = {"profit", "laba", "naik", "dividen", "akuisisi", "growth",
                         "bullish", "up", "gain", "record", "buy", "strong"}
            _NEGATIVE = {"rugi", "anjlok", "turun", "gugatan", "pkpu", "suspensi",
                         "loss", "down", "bearish", "sell", "weak", "default"}

            def _score_title(text: str) -> float:
                words = set(text.lower().split())
                pos = len(words & _POSITIVE)
                neg = len(words & _NEGATIVE)
                if pos + neg == 0:
                    return 0.0
                return (pos - neg) / (pos + neg)

        sentiment_scores = []
        for article in news[:10]:
            if isinstance(article, dict):
                if 'content' in article and isinstance(article['content'], dict):
                    title = article['content'].get("title", "")
                else:
                    title = article.get("title", "")
            else:
                title = str(article)

            if title and len(title) > 5:
                sentiment_scores.append(_score_title(title))

        if sentiment_scores:
            avg_sentiment = float(np.mean(sentiment_scores))
            sentiment_result["sentiment_score"] = avg_sentiment
            sentiment_result["news_count"] = len(sentiment_scores)
            sentiment_result["positive_news"] = len([s for s in sentiment_scores if s > 0.05])
            sentiment_result["negative_news"] = len([s for s in sentiment_scores if s < -0.05])

            if avg_sentiment > 0.05:
                sentiment_result["sentiment_label"] = "POSITIVE"
            elif avg_sentiment < -0.05:
                sentiment_result["sentiment_label"] = "NEGATIVE"
            else:
                sentiment_result["sentiment_label"] = "NEUTRAL"

    except Exception:
        pass

    return sentiment_result


def validasi_data_yfinance(df: pd.DataFrame, ticker: str) -> bool:
    """Membuang data cacat dari yfinance sebelum merusak kalkulasi AI"""
    if df.empty or len(df) < 50:
        return False

    # 1. Deteksi Saham Tidur (Suspend / Gocap)
    if df['Volume'].tail(10).sum() == 0:
        logger.warning("[SANITIZER] %s dilewati -- volume=0 10 hari (suspend/gocap)", ticker)
        return False

    # 1b. Deteksi harga flat
    harga_10d = df['Close'].tail(10)
    if harga_10d.max() == harga_10d.min():
        logger.warning("[SANITIZER] %s dilewati -- harga flat 10 hari (suspend/error data)", ticker)
        return False

    # 2. Deteksi Anomali Stock Split/Right Issue
    pct_change = df['Close'].tail(5).pct_change().abs() * 100
    anomali = pct_change[pct_change > 40.0]

    if not anomali.empty:
        logger.warning("[SANITIZER] %s dilewati -- anomali data ekstrem terdeteksi (kemungkinan cacat YF).", ticker)
        return False

    return True


# ── Alias untuk backward compatibility ─────────────────────────────────
fetch_price_data = fetch_price_data_sync


# ── Lazy stub functions (from screener.py, provided for reference) ──────
# These are lazy-import wrappers that screener.py still holds.
# fetch_foreign_flow_real is expected to be available from foreign_flow module.
try:
    from foreign_flow import fetch_foreign_flow_real
except ImportError:
    def fetch_foreign_flow_real(ticker):
        return {"net_foreign_5d": 0.0, "net_foreign_pct": 0.0, "foreign_status": "NEUTRAL", "source": "fallback"}
