"""
============================================================
  THE TIME MACHINE (BACKTESTER DATA LAKE PARQUET)
  Otomatisasi pengisian database menggunakan logika screener
  [VERSI TURBO + ANTI DATA LEAKAGE + AI PHASE 2]
============================================================
"""

import pandas as pd
import yfinance as yf
import datetime
import sys
import warnings
import os
from colorama import Fore, Style, init
from concurrent.futures import ThreadPoolExecutor, as_completed

init(autoreset=True)
warnings.filterwarnings("ignore")

print(f"{Fore.CYAN}{Style.BRIGHT}⏳ Menyiapkan Mesin Waktu...{Style.RESET_ALL}\n")

# 1. MENGIMPOR LOGIKA ASLI MILIKMU TANPA MENGUBAHNYA
try:
    # SESUAIKAN DENGAN NAMA FILE SCREENER TERBARUMU
    import screener as screener 
except ImportError:
    try:
        import screener # Fallback jika namanya screener.py
    except ImportError:
        sys.exit(f"{Fore.RED}Gagal mengimpor file screener. Pastikan file ini berada di folder yang sama.")

# Mematikan cache dan AI sementara agar tidak bentrok dengan simulasi
screener.USE_CACHE = False 
screener.CACHE_AVAILABLE = False
if hasattr(screener, 'AI_AKTIF'):
    screener.AI_AKTIF = False 

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
# 3. PEMBAJAKAN FUNGSI (MONKEY PATCHING) - ANTI KEBOCORAN MASA DEPAN
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

def mock_analisis_broksum(ticker):
    return {"status_bandar": "NEUTRAL", "akumulasi_bersih": 0}

def mock_get_sentiment(ticker):
    return 0.0, "NEUTRAL"

def mock_fetch_berita_lokal(ticker):
    return 0

screener.fetch_price_data = mock_fetch_price_data
screener.fetch_news_sentiment = mock_news_sentiment
screener.fetch_fundamental_metrics = mock_fundamentals
screener.analisis_broksum = mock_analisis_broksum
screener.get_sentiment = mock_get_sentiment
screener.fetch_berita_lokal = mock_fetch_berita_lokal

# ==============================================================================
# 4. EKSEKUSI MESIN WAKTU & MENYIMPAN KE PARQUET DATA LAKE
# ==============================================================================
os.makedirs("data_lake", exist_ok=True)
file_parquet = "data_lake/histori_ihsg.parquet"

# HAPUS DATABASE LAMA (Mulai dari Nol)
if os.path.exists(file_parquet):
    os.remove(file_parquet)
    print(f"{Fore.YELLOW}🗑️ File Parquet lama dihapus agar simulasi bersih dari data campuran.{Style.RESET_ALL}\n")

ihsg_kalender = HISTORICAL_DATA.get("^JKSE", pd.DataFrame())
if ihsg_kalender.empty:
    sys.exit(f"{Fore.RED}Gagal mengunduh kalender IHSG. Pastikan koneksi internet stabil.")

hari_simulasi = ihsg_kalender.index[ihsg_kalender.index >= pd.to_datetime(TANGGAL_MULAI_SIMULASI)]
total_hari = len(hari_simulasi)

print("3️⃣ MEMULAI MESIN WAKTU (Memindai hari demi hari dari awal 2024)...")

master_data_list = [] # List untuk menampung semua dataframe harian

for i, date in enumerate(hari_simulasi, 1):
    SIMULATION_DATE = date
    print(f"   [{i:03d}/{total_hari}] Memindai pasar tanggal: {date.date()}...", end="\r")
    
    # --- PERBAIKAN PARADOKS WAKTU: INJEKSI MAKRO & SEKTOR DINAMIS ---
    ihsg_hist = HISTORICAL_DATA.get("^JKSE", pd.DataFrame()).loc[:SIMULATION_DATE]
    usd_hist = HISTORICAL_DATA.get("IDR=X", pd.DataFrame()).loc[:SIMULATION_DATE]
    
    if len(ihsg_hist) >= 20:
        screener.IHSG_CHANGE = float((ihsg_hist['Close'].iloc[-1] - ihsg_hist['Close'].iloc[-2]) / ihsg_hist['Close'].iloc[-2] * 100)
        ihsg_ma20 = ihsg_hist['Close'].rolling(20).mean().iloc[-1]
        screener.IHSG_TREND = "UP" if float(ihsg_hist['Close'].iloc[-1]) > float(ihsg_ma20) else "DOWN"
        
    sektor_mom = {}
    for sektor, daftar_saham in screener.WATCHLIST_SEKTOR.items():
        # Abaikan kategori Pantauan Khusus (sesuaikan dengan nama di screener.py)
        if "Pantauan" in sektor: continue
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
    
    # 🚀 MENGAKTIFKAN MULTITHREADING: Gunakan semua CPU Core yang tersedia
    jumlah_core = os.cpu_count() or 4
    with ThreadPoolExecutor(max_workers=jumlah_core) as executor:
        futures = {executor.submit(screener.analisis_saham, t): t for t in screener.SEMUA_TICKER}
        for future in as_completed(futures):
            try:
                res = future.result()
                if res:
                    res["Tanggal"] = date.date().isoformat()
                    hasil_harian.append(res)
            except Exception:
                pass
            
    if hasil_harian:
        df_harian = pd.DataFrame(hasil_harian)
        
        # 🟢 UPGRADE: MENAMBAHKAN 3 FITUR BARU KE DALAM KOLOM AMAN
        kolom_aman = [
            "Ticker", "Sektor", "Harga", "Skor", "Sinyal", "Strength", 
            "Confidence%", "RSI", "ADX", "Stoch", "MACD", "Volume", 
            "Regime", "MM_Activity", "MM_Confidence", "Dominance",
            "Stop_Loss", "Target_1", "RRR",
            "BB_Width%", "MM_vs_Retail_Ratio", "IHSG_Change", "USD_Change", 
            "RSI_1d", "MACD_1d", "RSI_Vol_Interaction", "Rolling_Vol_20", "Sector_Corr", "Tanggal"
        ]
        
        existing_cols = [c for c in kolom_aman if c in df_harian.columns]
        df_harian_clean = df_harian[existing_cols].copy()
        
        master_data_list.append(df_harian_clean)

if master_data_list:
    print("\n\n4️⃣ MENYIMPAN HASIL KE DATA LAKE (PARQUET)...")
    df_gabungan = pd.concat(master_data_list, ignore_index=True)
    try:
        # Menyimpan dengan kompresi Snappy agar file ringan
        df_gabungan.to_parquet(file_parquet, engine='pyarrow', compression='snappy')
        print(f"{Fore.GREEN}{Style.BRIGHT}🎉 SUKSES! Data Lake Parquet telah terisi {len(df_gabungan):,} baris data historis.{Style.RESET_ALL}")
        print(f"Data tersimpan di: {os.path.abspath(file_parquet)}")
        print("\nTahap Selanjutnya:")
        print("Jalankan file bot AI-mu (misal: train_swing.py) untuk melatih ulang Otaknya dengan tumpukan data ini!")
    except Exception as e:
        print(f"{Fore.RED}Gagal menyimpan Parquet. Error: {e}{Style.RESET_ALL}")
else:
    print(f"\n{Fore.RED}Tidak ada data yang berhasil dikumpulkan.{Style.RESET_ALL}")
