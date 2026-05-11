import yfinance as yf
import pandas as pd
import time
import os

# Konfigurasi Cache
CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

USE_CACHE = True

def get_cache_key(ticker, period, interval):
    """Menghasilkan nama file unik untuk cache."""
    return f"{ticker}_{period}_{interval}"

def load_from_cache(cache_key):
    """Mengambil data dari folder cache jika tersedia."""
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
    """Menyimpan data ke folder cache."""
    if not USE_CACHE:
        return
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.pkl")
    try:
        data.to_pickle(cache_file)
    except:
        pass

def fetch_price_data_sync(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """Mengambil data harga saham menggunakan Yahoo Finance."""
    cache_key = get_cache_key(ticker, period, interval)
    
    # Cek cache dulu agar hemat waktu/bandwidth
    data = load_from_cache(cache_key)
    if data is not None and not data.empty:
        return data

    try:
        # Tambahkan .JK jika belum ada (untuk saham Indonesia)
        if not ticker.endswith(".JK") and len(ticker) <= 4:
            ticker = f"{ticker.upper()}.JK"
            
        time.sleep(0.1) # Jeda singkat agar tidak dianggap spam oleh server YF
        tkr = yf.Ticker(ticker)
        data = tkr.history(period=period, interval=interval)
        
        if data.empty:
            print(f"⚠️ [YFinance] Data {ticker} tidak ditemukan.")
            return pd.DataFrame()

        # Pembersihan Data (Data Cleaning)
        data.index = pd.to_datetime(data.index).tz_localize(None)
        
        # Pastikan kolom standar tersedia
        if 'Volume' not in data.columns:
            data['Volume'] = 0
            
        data = data[['Open', 'High', 'Low', 'Close', 'Volume']]
        
        # Simpan hasil sukses ke cache
        save_to_cache(cache_key, data)
        print(f"✅ [YFinance] Berhasil tarik {ticker} ({len(data)} hari)")
        return data
        
    except Exception as e:
        print(f"❌ [YFinance] Error saat mengambil {ticker}: {e}")
        return pd.DataFrame()

def fetch_multiple_tickers_sync(tickers: list, period: str = "6mo", interval: str = "1d") -> dict:
    """Mengambil banyak saham sekaligus secara berurutan."""
    results = {}
    for ticker in tickers:
        results[ticker] = fetch_price_data_sync(ticker, period, interval)
    return results

def fetch_macro_data() -> dict:
    """Mengambil data makro ekonomi global (IHSG, S&P500, USD, Komoditas)."""
    try:
        # Download semua data makro sekaligus
        macro_list = ["^JKSE", "^GSPC", "IDR=X", "BZ=F", "GC=F", "MTF=F"]
        macro_data = yf.download(macro_list, period="2mo", progress=False)['Close']
        
        # Ekstraksi data masing-masing
        jkse_clean = macro_data["^JKSE"].dropna() if "^JKSE" in macro_data.columns else pd.Series()
        gspc_clean = macro_data["^GSPC"].dropna() if "^GSPC" in macro_data.columns else pd.Series()
        idr_clean = macro_data["IDR=X"].dropna() if "IDR=X" in macro_data.columns else pd.Series()
        brent_clean = macro_data["BZ=F"].dropna() if "BZ=F" in macro_data.columns else pd.Series()
        gold_clean = macro_data["GC=F"].dropna() if "GC=F" in macro_data.columns else pd.Series()
        coal_clean = macro_data["MTF=F"].dropna() if "MTF=F" in macro_data.columns else pd.Series()

        # Fungsi pembantu untuk hitung % perubahan
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
        print(f"⚠️ [Macro] Gagal mengambil data makro: {e}")
        return {
            "ihsg_change": 0.0, "sp500_change": 0.0, "usd_change": 0.0,
            "brent_change": 0.0, "gold_change": 0.0, "coal_change": 0.0,
            "usd_price": 16000.0, "ihsg_data": pd.Series()
        }