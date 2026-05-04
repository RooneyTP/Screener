"""
============================================================
  THE TIME MACHINE (BACKTESTER DATABASE SQLITE)
  Otomatisasi pengisian database menggunakan logika screener.py
============================================================
"""

import pandas as pd
import yfinance as yf
import sqlite3
import datetime
import sys
import warnings
import os
from colorama import Fore, Style, init

init(autoreset=True)
warnings.filterwarnings("ignore")

print(f"{Fore.CYAN}{Style.BRIGHT}⏳ Menyiapkan Mesin Waktu...{Style.RESET_ALL}\n")

# 1. MENGIMPOR LOGIKA ASLI MILIKMU TANPA MENGUBAHNYA
try:
    import screener
except ImportError:
    sys.exit(f"{Fore.RED}Gagal mengimpor screener.py. Pastikan file ini berada di folder yang sama.")

# Mematikan cache dan AI sementara agar tidak bentrok dengan simulasi
screener.USE_CACHE = False 
screener.CACHE_AVAILABLE = False
screener.AI_MODEL = None 

TANGGAL_MULAI_DOWNLOAD = "2023-06-01" 
TANGGAL_MULAI_SIMULASI = "2024-01-01" 

# ==============================================================================
# 2. PENGUMPULAN DATA MASSAL (BATCH DOWNLOAD - SUPER CEPAT)
# ==============================================================================
print("1️⃣ MENGUNDUH DATA MASSAL (BATCH DOWNLOAD)...")
# Masukkan juga IHSG dan USD ke dalam antrean download
semua_ticker_full = [t if t.endswith(".JK") else f"{t}.JK" for t in screener.SEMUA_TICKER]
semua_ticker_full += ["^JKSE", "IDR=X"] 

data_raw = yf.download(semua_ticker_full, start=TANGGAL_MULAI_DOWNLOAD, progress=False)

HISTORICAL_DATA = {}
if not data_raw.empty:
    # Pisahkan format MultiIndex YFinance menjadi per saham
    for tkr in semua_ticker_full:
        try:
            if len(semua_ticker_full) == 1:
                df_saham = data_raw.copy()
            else:
                df_saham = pd.DataFrame({
                    "Open": data_raw["Open"][tkr],
                    "High": data_raw["High"][tkr],
                    "Low": data_raw["Low"][tkr],
                    "Close": data_raw["Close"][tkr],
                    "Volume": data_raw["Volume"][tkr]
                }).dropna()
            
            if not df_saham.empty:
                if df_saham.index.tz is not None:
                    df_saham.index = df_saham.index.tz_localize(None)
                HISTORICAL_DATA[tkr] = df_saham
        except:
            pass

print("2️⃣ MENYIAPKAN CACHE FUNDAMENTAL...")
FUNDAMENTALS_CACHE = {}
# Fundamental statis masa lalu kita asumsikan kosong/minimal agar cepat
for ticker in screener.SEMUA_TICKER:
    FUNDAMENTALS_CACHE[ticker] = {"float_shares": 0, "shares_outstanding": 0}

print(f"{Fore.GREEN}✓ Seluruh data berhasil diamankan!{Style.RESET_ALL}\n")

# ==============================================================================
# 3. PEMBAJAKAN FUNGSI (MONKEY PATCHING)
# ==============================================================================
SIMULATION_DATE = None

def mock_fetch_price_data(ticker, period="6mo", interval="1d", retries=3):
    full_tkr = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
    df = HISTORICAL_DATA.get(full_tkr)
    if df is None or df.empty: return pd.DataFrame()
    
    # Potong masa depan yang belum terjadi!
    sliced = df.loc[:SIMULATION_DATE]
    try:
        if interval == "1wk":
            sliced = sliced.resample('W').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last', 'Volume':'sum'}).dropna()
        elif interval == "1mo":
            sliced = sliced.resample('ME').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last', 'Volume':'sum'}).dropna()
    except: pass
    if len(sliced) > 130: sliced = sliced.tail(130)
    return sliced

def mock_news_sentiment(ticker):
    return {"sentiment_score": 0.0, "sentiment_label": "NEUTRAL", "news_count": 0}

def mock_fundamentals(ticker):
    return FUNDAMENTALS_CACHE.get(ticker, {"float_shares": 0, "shares_outstanding": 0})

screener.fetch_price_data = mock_fetch_price_data
screener.fetch_news_sentiment = mock_news_sentiment
screener.fetch_fundamental_metrics = mock_fundamentals

# ==============================================================================
# 4. EKSEKUSI MESIN WAKTU & PERBAIKAN PARADOKS WAKTU
# ==============================================================================
# HAPUS DATABASE LAMA (Mulai dari Nol)
if os.path.exists("histori_ihsg.db"):
    os.remove("histori_ihsg.db")
    print(f"{Fore.YELLOW}🗑️ Database lama dihapus agar simulasi bersih dari data campuran.{Style.RESET_ALL}\n")

conn = sqlite3.connect("histori_ihsg.db")

ihsg_kalender = HISTORICAL_DATA.get("^JKSE", pd.DataFrame())
if ihsg_kalender.empty:
    sys.exit(f"{Fore.RED}Gagal mengunduh kalender IHSG. Pastikan koneksi internet stabil.")

hari_simulasi = ihsg_kalender.index[ihsg_kalender.index >= pd.to_datetime(TANGGAL_MULAI_SIMULASI)]
total_hari = len(hari_simulasi)

print("3️⃣ MEMULAI MESIN WAKTU (Memindai hari demi hari dari awal 2024)...")
for i, date in enumerate(hari_simulasi, 1):
    SIMULATION_DATE = date
    print(f"   [{i:03d}/{total_hari}] Memindai pasar tanggal: {date.date()}...", end="\r")
    
    # --- PERBAIKAN PARADOKS WAKTU: INJEKSI MAKRO & SEKTOR DINAMIS ---
    # Memaksa screener.py menggunakan data IHSG dan Sektor sesuai tanggal simulasi
    ihsg_hist = HISTORICAL_DATA.get("^JKSE", pd.DataFrame()).loc[:SIMULATION_DATE]
    usd_hist = HISTORICAL_DATA.get("IDR=X", pd.DataFrame()).loc[:SIMULATION_DATE]
    
    if len(ihsg_hist) >= 20:
        screener.IHSG_CHANGE = float((ihsg_hist['Close'].iloc[-1] - ihsg_hist['Close'].iloc[-2]) / ihsg_hist['Close'].iloc[-2] * 100)
        ihsg_ma20 = ihsg_hist['Close'].rolling(20).mean().iloc[-1]
        screener.IHSG_TREND = "UP" if float(ihsg_hist['Close'].iloc[-1]) > float(ihsg_ma20) else "DOWN"
        
    sektor_mom = {}
    for sektor, daftar_saham in screener.WATCHLIST_SEKTOR.items():
        if sektor == "Pantauan": continue
        ret_sektor = []
        for tkr in daftar_saham:
            full_t = tkr if tkr.endswith(".JK") else f"{tkr}.JK"
            saham_hist = HISTORICAL_DATA.get(full_t)
            if saham_hist is not None and not saham_hist.empty:
                s_cut = saham_hist.loc[:SIMULATION_DATE]
                if len(s_cut) >= 5:
                    pct = (s_cut['Close'].iloc[-1] - s_cut['Close'].iloc[-5]) / s_cut['Close'].iloc[-5] * 100
                    ret_sektor.append(float(pct))
        sektor_mom[sektor] = sum(ret_sektor)/len(ret_sektor) if ret_sektor else 0.0
    screener.SEKTOR_MOMENTUM = sektor_mom
    # -------------------------------------------------------------------

    hasil_harian = []
    for ticker in screener.SEMUA_TICKER:
        res = screener.analisis_saham(ticker) 
        if res:
            res["Tanggal"] = date.date().isoformat()
            hasil_harian.append(res)
            
    if hasil_harian:
        df_harian = pd.DataFrame(hasil_harian)
        if "Max" in df_harian.columns: df_harian = df_harian.drop(columns=["Max"])
        if "AI_Win_Prob%" in df_harian.columns: df_harian = df_harian.drop(columns=["AI_Win_Prob%"])
        if "AI_Verdict" in df_harian.columns: df_harian = df_harian.drop(columns=["AI_Verdict"])
        
        df_harian.to_sql("hasil_screener", conn, if_exists="append", index=False)

conn.close()
print(f"\n\n{Fore.GREEN}{Style.BRIGHT}🎉 SUKSES! Database telah terisi riwayat penuh 70+ saham baru.{Style.RESET_ALL}")
print("Silakan jalankan 'python auto_train.py' untuk melatih ulang Otak AI-mu dengan data masif ini!")