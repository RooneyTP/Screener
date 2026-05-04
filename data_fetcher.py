import yfinance as yf
import pandas as pd
import time
import os

CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

USE_CACHE = True

def get_cache_key(ticker, period, interval):
    return f"{ticker}_{period}_{interval}"

def load_from_cache(cache_key):
    if not USE_CACHE:
        return None
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.pkl")
    if os.path.exists(cache_file):
        try:
            return pd.read_pickle(cache_file)
        except:
            return None
    return None

def save_to_cache(cache_key, data):
    if not USE_CACHE:
        return
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.pkl")
    try:
        data.to_pickle(cache_file)
    except:
        pass

def fetch_price_data_sync(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    cache_key = get_cache_key(ticker, period, interval)
    data = load_from_cache(cache_key)
    if data is not None and not data.empty:
        return data

    try:
        time.sleep(1)
        tkr = yf.Ticker(ticker)
        data = tkr.history(period=period, interval=interval)

        if data is not None and not data.empty:
            if data.index.tz is not None:
                data.index = data.index.tz_localize(None)
            data = data.dropna()
            save_to_cache(cache_key, data)
            return data
    except Exception as e:
        print(f"Error fetching {ticker}: {e}")

    return pd.DataFrame()

def fetch_multiple_tickers_sync(tickers: list, period: str = "6mo", interval: str = "1d") -> dict:
    """Fetch multiple tickers synchronously with caching"""
    results = {}
    for ticker in tickers:
        results[ticker] = fetch_price_data_sync(ticker, period, interval)
    return results

def fetch_macro_data() -> dict:
    try:
        macro_data = yf.download(["^JKSE", "^GSPC", "IDR=X", "BZ=F", "GC=F", "MTF=F"], period="2mo", progress=False)['Close']

        jkse_clean = macro_data["^JKSE"].dropna() if "^JKSE" in macro_data.columns else pd.Series()
        gspc_clean = macro_data["^GSPC"].dropna() if "^GSPC" in macro_data.columns else pd.Series()
        idr_clean = macro_data["IDR=X"].dropna() if "IDR=X" in macro_data.columns else pd.Series()
        brent_clean = macro_data["BZ=F"].dropna() if "BZ=F" in macro_data.columns else pd.Series()
        gold_clean = macro_data["GC=F"].dropna() if "GC=F" in macro_data.columns else pd.Series()
        coal_clean = macro_data["MTF=F"].dropna() if "MTF=F" in macro_data.columns else pd.Series()

        return {
            "IHSG_CHANGE": float((jkse_clean.iloc[-1] - jkse_clean.iloc[-2]) / jkse_clean.iloc[-2] * 100) if len(jkse_clean) >= 2 else 0.0,
            "SP500_CHANGE": float((gspc_clean.iloc[-1] - gspc_clean.iloc[-2]) / gspc_clean.iloc[-2] * 100) if len(gspc_clean) >= 2 else 0.0,
            "USD_CHANGE": float((idr_clean.iloc[-1] - idr_clean.iloc[-2]) / idr_clean.iloc[-2] * 100) if len(idr_clean) >= 2 else 0.0,
            "BRENT_CHANGE": float((brent_clean.iloc[-1] - brent_clean.iloc[-2]) / brent_clean.iloc[-2] * 100) if len(brent_clean) >= 2 else 0.0,
            "GOLD_CHANGE": float((gold_clean.iloc[-1] - gold_clean.iloc[-2]) / gold_clean.iloc[-2] * 100) if len(gold_clean) >= 2 else 0.0,
            "COAL_CHANGE": float((coal_clean.iloc[-1] - coal_clean.iloc[-2]) / coal_clean.iloc[-2] * 100) if len(coal_clean) >= 2 else 0.0,
            "USD_PRICE": float(idr_clean.iloc[-1]) if len(idr_clean) >= 1 else 16000.0,
            "IHSG_DATA": jkse_clean
        }
    except Exception as e:
        print(f"Macro data error: {e}")
        return {
            "IHSG_CHANGE": 0.0, "SP500_CHANGE": 0.0, "USD_CHANGE": 0.0,
            "BRENT_CHANGE": 0.0, "GOLD_CHANGE": 0.0, "COAL_CHANGE": 0.0,
            "USD_PRICE": 16000.0, "IHSG_DATA": pd.Series()
        }