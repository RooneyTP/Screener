"""
============================================================
  IHSG STOCK SCREENER v9.0 - The Quant Ninja
  ⭐⭐⭐ COMPLETE MARKET PREDICTION SYSTEM WITH AI:

  TIER 1 - ADVANCED INDICATORS (18 total)
  TIER 2 - MACHINE LEARNING INTEGRATION (Random Forest)
  TIER 3 - MARKET MAKER ANALYSIS
  TIER 4 - PORTFOLIO MANAGEMENT
  TIER 5 - PERFORMANCE ANALYTICS & DATABASE SQLITE
  TIER 6 - SENTIMENT ANALYSIS
  TIER 7 - Global Macro Sensor + Virtual Hedge Fund + Deep Learning AI
  TIER 8 - Liquid Neural Network + Mixture of Experts + Pipe Data 3D + Data Padding &
  TIER 9 - Intermarket Analysis + Z-Score Detection + Local News + Foreignflow + Time Encoding + Global Commodities Sensor
============================================================
"""

import sys
import io

# Fix UTF-8 encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# FIX BUG 1: pandas harus diimport dulu sebelum dipakai di bawah
import pandas as pd

# Import custom modules
from indicators import *
from ai_model import get_ai_model
from data_fetcher import fetch_macro_data, fetch_price_data_sync
from security import *
from performance import *
import data_fetcher
from broker_scraper import analisis_broksum
# =======================================================
# SENSOR MARKET BREADTH v9.2 (Lazy Loading)
# =======================================================
global_total_discan = 0
global_saham_uptrend = 0

# Lazy load macro data on first use
_macro_data_cache = None

def get_macro_data():
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
            ihsg_data = pd.Series(ihsg_series) if not isinstance(ihsg_series, pd.Series) else ihsg_series

        # Hitung MACRO_PENALTY dari kondisi riil
        penalty = 0.0
        if IHSG_CHANGE  < -1.5: penalty -= 1.0
        if SP500_CHANGE < -1.0: penalty -= 0.5
        if USD_CHANGE   >  0.5: penalty -= 0.5
        if BRENT_CHANGE >  3.0: penalty -= 0.5
        if IHSG_CHANGE  >  1.0: penalty += 0.5
        if GOLD_CHANGE  >  1.0: penalty -= 0.5
        MACRO_PENALTY = round(penalty, 1)

        # Update IHSG_TREND
        if len(ihsg_data) >= 3:
            last3 = ihsg_data.tail(3)
            IHSG_TREND = "UP" if (last3.diff().dropna() > 0).all() else "DOWN"
        else:
            IHSG_TREND = "UP" if IHSG_CHANGE > 0 else "DOWN"

        # Phase-4: use logger instead of print for structured output
        logger.info(
            "Macro update — IHSG=%+.2f%% | USD=%+.2f%% | Trend=%s | Penalty=%.1f",
            IHSG_CHANGE, USD_CHANGE, IHSG_TREND, MACRO_PENALTY
        )
    except Exception as e:
        # Phase-1: log with full traceback instead of silent swallow
        logger.error("update_macro_globals() gagal: %s", e, exc_info=True)

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
# FIX BUG 4: IHSG_TREND tidak pernah didefinisikan — tambahkan default di sini
IHSG_TREND = "UP"
# Discord webhook URL — isi dengan URL webhook Discord kamu, atau biarkan kosong untuk skip
# DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1497448578312835082/L_lkCmGrKEByeKwHeRaoycT9JS2QGjU_Mln6sekuzEvhBlOgkiwgfi8_NBww0iHgrD8G"
import numpy as np
import datetime
import warnings
import os
import json
import argparse
from nlp_scraper import get_sentiment
import time
import requests
import traceback
import sqlite3
import logging
import gc  # Phase-3: batch memory management
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Phase-4: Unified logging setup with timestamp ────────────────────────────
# All print() for errors/warnings are replaced with logger calls throughout.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("screener")

# Suppress yfinance and urllib3 logging to avoid noise
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("requests").setLevel(logging.CRITICAL)

# ─── New Imports for Upgrades ────────────────────────────────────────────────
try:
    import joblib
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    SENTIMENT_AVAILABLE = True
except ImportError:
    SENTIMENT_AVAILABLE = False

try:
    from scipy.optimize import minimize
    OPTIMIZATION_AVAILABLE = True
except ImportError:
    OPTIMIZATION_AVAILABLE = False

try:
    import smtplib
    from email.mime.text import MIMEText
    ALERTS_AVAILABLE = True
except ImportError:
    ALERTS_AVAILABLE = False

warnings.filterwarnings("ignore")

try:
    from ta.trend import SMAIndicator, EMAIndicator, MACD, ADXIndicator
    from ta.momentum import RSIIndicator, StochasticOscillator
    from ta.volatility import BollingerBands, AverageTrueRange
    from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice
except ImportError:
    os.system(f"{sys.executable} -m pip install ta -q")
    from ta.trend import SMAIndicator, EMAIndicator, MACD, ADXIndicator
    from ta.momentum import RSIIndicator, StochasticOscillator
    from ta.volatility import BollingerBands, AverageTrueRange
    from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice

# FIX BUG 2: yf (yfinance) dipakai di bawah tapi tidak pernah diimport
try:
    import yfinance as yf
except ImportError:
    os.system(f"{sys.executable} -m pip install yfinance -q")
    import yfinance as yf

warnings.filterwarnings("ignore")

# ==========================================
# ⚙️ STRATEGY ENGINE CONFIG (BALANCED WEIGHTS v10)
# ==========================================
# REVAMP: Tech 35% → Fund 25% → RS 20% → Sentiment 20%
BOBOT_SKOR = {
    # TIER 1: TECHNICAL INDICATORS (35% weight) ═══════════════════════
    "EMA_Aligned": 2.0,        # Price > EMA21 > EMA50 > HMA (strongest)
    "RSI_Good_Entry": 1.5,     # RSI 30-50 range (not overbought)
    "MACD_Bullish": 1.5,       # MACD histogram positive & increasing
    "Volume_Confirm": 2.0,     # Volume > SMA20 (confirmation)
    "ADX_Strong": 1.5,         # ADX > 35 (trending market)
    "VCP_Pattern": 1.0,        # Volatility contraction pattern
    "Vol_Anomaly": 1.5,        # Volume Z-score anomaly (rebalanced)
    
    # TIER 2: FUNDAMENTAL ANALYSIS (25% weight) ═════════════════════════
    "PER_Cheap": 2.0,          # PER <= 12 (significantly undervalued)
    "PER_Fair": 1.0,           # PER 12-18 (reasonable valuation)
    "PBV_Strong": 1.5,         # PBV <= 1.0 (solid asset value)
    "PBV_Mahal": -1.0,         # PBV > 5.0 (too expensive - symmetric penalty)
    "Earnings_Quality": 1.5,   # Growing profits (added)
    
    # TIER 3: RELATIVE STRENGTH (20% weight) ════════════════════════════
    "RS_Outperform": 1.5,      # Stock > IHSG return (20d)
    "Sector_Leadership": 1.5,  # Leading in sector (from HOT)
    "Alpha_Leader": 1.5,       # Gaining market share
    "RS_Top_Decile": 1.0,      # Top 10% strongest stocks
    
    # TIER 4: SENTIMENT & MARKET PSYCHOLOGY (20% weight) ═════════════════
    "News_BULLISH": 1.5,       # Positive news (rebalanced UP)
    "News_BEARISH": -1.5,      # Negative news (SYMMETRIC)
    "Sentiment_Strong": 1.0,   # Strong sentiment score (added)
    "Foreign_Buy": 1.5,        # Foreign accumulation
    "Foreign_Sell": -1.5,      # Foreign selling (SYMMETRIC)
    
    # MICRO PATTERNS & CONFIRMATION (5% weight) ═════════════════════════
    "Pullback_EMA21": 1.0,     # Healthy pullback (reduced)
    "Wyckoff_Absorb": 1.0,     # Effort vs result (reduced)
    "Sector_Cold": -1.0,       # Cold sector rotation
    "Overbought": -1.5,        # Bollinger top (strict)
    
    # RISK PENALTIES (Critical Safety Filters) ══════════════════════════
    "EPS_Minus": -2.0,         # No earnings (stronger penalty)
    "Delisting_Risk": -3.0,    # Bankruptcy/delisting warning
    "Broksum_ACCUM": 1.5,      # Broker accumulation
    "Broksum_DIST": -1.5       # Broker distribution (SYMMETRIC)
}

# =====================================================================
# 🧠 MEMBANGUNKAN OTAK LIQUID MOE
# =====================================================================
# AI Model sekarang lazy-loaded di ai_model.py (no module-level output)
AI_AKTIF = True  # Akan di-check saat digunakan

# ─── Caching Setup ───────────────────────────────────────────────────────────
CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

USE_CACHE = False  
DEFAULT_MAX_WORKERS = min(8, max(2, (os.cpu_count() or 4)))
EXPORT_FILENAME_TEMPLATE = "screener_ihsg_{date}.csv"

def get_cache_key(ticker, period, interval):
    return f"{ticker}_{period}_{interval}_{datetime.date.today().isoformat()}"

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

def load_from_cache(cache_key):
    if not CACHE_AVAILABLE or not USE_CACHE:
        return None
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.pkl")
    if os.path.exists(cache_file):
        try:
            return joblib.load(cache_file)
        except:
            return None
    return None

def save_to_cache(cache_key, data):
    if not CACHE_AVAILABLE or not USE_CACHE:
        return
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.pkl")
    try:
        joblib.dump(data, cache_file)
    except:
        pass

# Use fetch_price_data from data_fetcher module
fetch_price_data = fetch_price_data_sync

def fetch_foreign_flow(ticker: str) -> dict:
    """
    [SLOT API MASA DEPAN]
    Saat ini Yahoo Finance tidak punya data Foreign Flow. 
    Nanti kamu bisa mengganti isi fungsi ini dengan script Scraper RTI / Stockbit / GoAPI.
    """
    result = {
        "net_foreign_5d": 0.0,  # Dalam Rupiah
        "foreign_status": "NEUTRAL" # ACCUMULATION / DISTRIBUTION
    }
    
    # CONTOH LOGIKA JIKA NANTI SUDAH PAKAI API:
    # try:
    #     url = f"https://api.punyamu.com/foreign/{ticker}"
    #     data = requests.get(url).json()
    #     result["net_foreign_5d"] = data["net_buy_5d"]
    #     result["foreign_status"] = "ACCUMULATION" if data["net_buy_5d"] > 0 else "DISTRIBUTION"
    # except:
    #     pass
        
    return result

def fetch_berita_lokal(ticker: str) -> int:
    """Scraper senyap menggunakan RSS Feed portal berita Indonesia (CNBC/Kontan)"""
    import urllib.request
    import xml.etree.ElementTree as ET
    score = 0
    try:
        # Mengambil data RSS terbaru
        url = "https://www.cnbcindonesia.com/market/rss"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            
            # Mencari nama saham di judul berita
            for item in root.findall('.//item/title'):
                title = (item.text or "").upper()
                if ticker.replace(".JK", "") in title:
                    # Analisis sentimen kata kunci ala Quant
                    if any(word in title for word in ["CUM CUAN", "LABA", "MEROKET", "AKUISISI", "DIVIDEN"]):
                        score += 1
                    elif any(word in title for word in ["RUGI", "ANJLOK", "GUGATAN", "PKPU", "SUSPENSI"]):
                        score -= 1
    except:
        pass
    return score

def detect_zscore_anomaly(series: pd.Series, window: int = 60) -> float:
    """Mendeteksi apakah volume hari ini adalah anomali ekstrem (Z-Score > 3)"""
    if len(series) < window: return 0.0
    mean = series.rolling(window).mean().iloc[-1]
    std = series.rolling(window).std().iloc[-1]
    if std == 0: return 0.0
    return float((series.iloc[-1] - mean) / std)

def hitung_kelly_sizing(ai_win_prob_percent: float, harga_saham: float, modal_trading: float = 10000000.0) -> str:
    """
    Menghitung porsi beli menggunakan Half-Kelly Criterion.
    Asumsi Risk/Reward Ratio (b) adalah 2.0 (Target Take Profit 2x lebih besar dari Stop Loss)
    """
    p = (ai_win_prob_percent) / 100.0  # Probabilitas menang (Win Rate AI)
    q = 1.0 - p                      # Probabilitas kalah
    b = 2.0                          # Risk/Reward Ratio
    
    # Rumus Kelly Fraction: f* = p - (q / b)
    kelly_fraction = p - (q / b)
    
    if kelly_fraction <= 0:
        return "0 Lot (Risiko Terlalu Tinggi)"
    
    # Kita gunakan Half-Kelly agar tidak terlalu agresif (Standar institusi)
    safe_kelly = kelly_fraction / 2.0 
    
    # Batasi maksimal 25% modal untuk 1 saham
    safe_kelly = min(safe_kelly, 0.25)
    
    alokasi_dana = modal_trading * safe_kelly
    jumlah_lot = int((alokasi_dana / harga_saham) / 100)
    
    return f"{safe_kelly*100:.1f}% Modal (Beli ±{max(1, jumlah_lot)} Lot)"

# ─── Warna Terminal (ANSI) ───────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    GRAY   = "\033[90m"
    MAGENTA = "\033[95m"
    BG_GREEN  = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_RED    = "\033[41m"

def warna_sinyal(sinyal: str) -> str:
    if "ULTRA" in sinyal:
        return f"{C.BG_GREEN}{C.BOLD}{C.WHITE} {sinyal} {C.RESET}"
    elif "STRONG" in sinyal:
        return f"{C.BG_GREEN}{C.BOLD}{C.WHITE} {sinyal} {C.RESET}"
    elif "BUY" in sinyal and "STRONG" not in sinyal:
        return f"{C.GREEN}{C.BOLD}{sinyal}{C.RESET}"
    elif "PANTAU" in sinyal:
        return f"{C.YELLOW}{sinyal}{C.RESET}"
    else:
        return f"{C.GRAY}{sinyal}{C.RESET}"

def safe_int(value, default=0):
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(float(value))
    except Exception:
        return default

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


# ─── Daftar Saham per Sektor ─────────────────────────────────────────────────
WATCHLIST_SEKTOR = {
    "Perbankan": [
        "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BRIS.JK", "BBTN.JK", 
        "BNGA.JK", "BDMN.JK", "NISP.JK", "BTPS.JK", "ARTO.JK", "CFIN.JK", 
        "BBYB.JK", "BVIC.JK", "BJTM.JK", "BJBR.JK", "PNBN.JK", "BSIM.JK"
    ],
    "Konglomerat & Investasi": [
        "ASII.JK", "SRTG.JK", "BMTR.JK", "BHIT.JK", "MLPL.JK", "SMMA.JK", 
        "ABMM.JK", "UNTR.JK", "TPIA.JK", "LPKR.JK", "MPPA.JK", "BNLI.JK",
        "SCMA.JK", "VIVA.JK", "ADMG.JK"
    ],
    "Teknologi & Telco": [
        "TLKM.JK", "ISAT.JK", "EXCL.JK", "GOTO.JK", "BUKA.JK", 
        "BELI.JK", "WIFI.JK", "EMTK.JK", "MLPT.JK", "MTDL.JK", "DMMX.JK",
        "KREN.JK", "AXIO.JK", "GLVA.JK"
    ],
    "Energi & Tambang": [
        "ADRO.JK", "ITMG.JK", "PTBA.JK", "INDY.JK", "HRUM.JK", "BUMI.JK", 
        "BRMS.JK", "DEWA.JK", "ENRG.JK", "MEDC.JK", "PGAS.JK", "AKRA.JK", 
        "ANTM.JK", "INCO.JK", "TINS.JK", "CUAN.JK", "MBMA.JK", "NCKL.JK",
        "KKGI.JK", "DOID.JK", "ADMR.JK", "RMKE.JK", "TOBA.JK"
    ],
    "Infrastruktur & Konstruksi": [
        "JSMR.JK", "PTPP.JK", "ADHI.JK", "WIKA.JK", "WSKT.JK", "WEGE.JK", 
        "PPRE.JK", "TOTL.JK", "ACST.JK", "JKON.JK", "META.JK", "CMNP.JK",
        "LEAD.JK", "RIGS.JK", "TPMA.JK", "SMDR.JK", "BIRD.JK"
    ],
    "Consumer & Retail": [
        "UNVR.JK", "ICBP.JK", "INDF.JK", "MYOR.JK", "GOOD.JK", "ROTI.JK", 
        "CAMP.JK", "CLEO.JK", "ADES.JK", "STTP.JK", "SIDO.JK", "KAEF.JK", 
        "PEHA.JK", "AMRT.JK", "MIDI.JK", "MAPI.JK", "MAPA.JK", "ACES.JK", 
        "ERAA.JK", "RALS.JK", "LPPF.JK", "MPPA.JK", "HOKI.JK", "CPIN.JK", "JPFA.JK", "ENZO.JK"
    ],
    "Properti & Real Estate": [
        "BSDE.JK", "CTRA.JK", "SMRA.JK", "PWON.JK", "ASRI.JK", "DMAS.JK", 
        "DUTI.JK", "DILD.JK", "PPRO.JK", "BKSL.JK", "GWSA.JK", "MKPI.JK",
        "LPCK.JK", "KIJA.JK", "SSIA.JK"
    ],
    "Kesehatan": [
        "KLBF.JK", "MIKA.JK", "HEAL.JK", "SILO.JK", "PRDA.JK", "DGNS.JK", 
        "BMHS.JK", "IRRA.JK", "TSPC.JK", "SAME.JK"
    ],
    "Industri Dasar & Logam": [
        "SMGR.JK", "INTP.JK", "SMBR.JK", "SMCB.JK", "KRAS.JK", "ISSP.JK", 
        "BAJA.JK", "NIKL.JK", "ALKA.JK", "BRNA.JK", "TOTO.JK"
    ],
    "Transportasi & Logistik": [
        "ASSA.JK", "BIRD.JK", "GIAA.JK", "TMAS.JK", "SMDR.JK", "NELY.JK", 
        "HAIS.JK", "PANI.JK", "BPTR.JK"
    ],
    "Agrikultur": [
        "AALI.JK", "LSIP.JK", "SIMP.JK", "BWPT.JK", "TAPG.JK", "DSNG.JK", 
        "TBLA.JK", "SSMS.JK", "ANJT.JK"
    ],
    "Pantauan Khusus (High Volatility)": [
        "ALII.JK", "PMUI.JK", "AREA.JK", "STRK.JK", "WIDI.JK", "AWAN.JK", 
        "HUMI.JK", "GTRA.JK", "MENN.JK"
    ],
}

SEMUA_TICKER = [t for sektor in WATCHLIST_SEKTOR.values() for t in sektor]
PETA_SEKTOR = {t: s for s, tlist in WATCHLIST_SEKTOR.items() for t in tlist}

# OPTIMIZATION: Initialize SEKTOR_MOMENTUM as empty dict (will be populated by lazy loading)
# This avoids long hang on module import when computing sector momentum
SEKTOR_MOMENTUM = {}
_SEKTOR_MOMENTUM_LOADED = False

def _download_batch(tickers_batch: list, period: str = "5d") -> pd.DataFrame:
    """Download batch kecil dengan timeout via concurrent.futures agar tidak hang."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    def _do_download():
        return yf.download(tickers_batch, period=period, progress=False, threads=False)
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_do_download)
        try:
            return future.result(timeout=30)   # max 30 detik per batch
        except (FuturesTimeout, Exception):
            return pd.DataFrame()

def compute_sector_momentum():
    """Lazy-load sector momentum (called once at start of jalankan_screener)"""
    global SEKTOR_MOMENTUM, _SEKTOR_MOMENTUM_LOADED
    
    if _SEKTOR_MOMENTUM_LOADED:
        return  # Already computed
    
    logger.info("Memindai Arus Uang (Rotasi Sektor)...")
    
    try:
        BATCH_SIZE = 25   # max ticker per request agar tidak freeze
        data_sektor_all: dict[str, pd.Series] = {}   # ticker -> Series harga close

        for sektor, daftar_saham in WATCHLIST_SEKTOR.items():
            if sektor == "Pantauan Khusus (High Volatility)":
                continue
            tickers_full = [t if t.endswith(".JK") else f"{t}.JK" for t in daftar_saham]

            # Download per batch
            for i in range(0, len(tickers_full), BATCH_SIZE):
                batch = tickers_full[i : i + BATCH_SIZE]
                try:
                    raw = _download_batch(batch)
                    if raw.empty:
                        continue

                    # yfinance mengembalikan MultiIndex kolom saat multi-ticker,
                    # kolom biasa saat single ticker
                    if isinstance(raw.columns, pd.MultiIndex):
                        close_df = raw["Close"] if "Close" in raw.columns.get_level_values(0) else pd.DataFrame()
                    else:
                        # Single ticker — bungkus jadi DataFrame dengan nama ticker
                        close_df = raw[["Close"]].rename(columns={"Close": batch[0]}) if "Close" in raw.columns else pd.DataFrame()

                    for tkr in batch:
                        if tkr in close_df.columns:
                            series = close_df[tkr].dropna()
                            if len(series) >= 2:
                                data_sektor_all[tkr] = series
                except Exception:
                    continue   # lewati batch yang gagal, lanjut ke batch berikutnya

        # Hitung momentum per sektor dari data yang sudah terkumpul
        for sektor, daftar_saham in WATCHLIST_SEKTOR.items():
            if sektor == "Pantauan Khusus (High Volatility)":
                continue
            ret_sektor = []
            for tkr in daftar_saham:
                full_t = tkr if tkr.endswith(".JK") else f"{tkr}.JK"
                series = data_sektor_all.get(full_t)
                if series is not None and len(series) >= 2:
                    try:
                        pct_change = (series.iloc[-1] - series.iloc[0]) / series.iloc[0] * 100
                        ret_sektor.append(float(pct_change))
                    except Exception:
                        pass
            SEKTOR_MOMENTUM[sektor] = sum(ret_sektor) / len(ret_sektor) if ret_sektor else 0.0

        logger.info("Rotasi sektor selesai (%d sektor dipindai)", len(SEKTOR_MOMENTUM))
        _SEKTOR_MOMENTUM_LOADED = True
    except Exception as e:
        logger.warning("Rotasi sektor gagal: %s — lanjut tanpa data momentum sektor.", e)
        SEKTOR_MOMENTUM = {}
        _SEKTOR_MOMENTUM_LOADED = True

# ─── Fungsi Helper untuk Analisis Lanjutan ───────────────────────────────────
def hma(data: pd.Series, period: int = 20) -> pd.Series:
    """Hull Moving Average yang benar menggunakan WMA."""
    def _wma(series: pd.Series, length: int) -> pd.Series:
        weights = np.arange(1, length + 1, dtype=float)
        return series.rolling(length).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )
    half_period = max(1, int(period / 2))
    sqrt_period = max(1, int(np.sqrt(period)))
    raw_hma = 2 * _wma(data, half_period) - _wma(data, period)
    return _wma(raw_hma, sqrt_period)

def detect_support_resistance(data: pd.Series, lookback: int = 20) -> tuple:
    support = data.rolling(lookback).min().iloc[-1]
    resistance = data.rolling(lookback).max().iloc[-1]
    return support, resistance

def market_regime(close: pd.Series, atr: pd.Series) -> str:
    avg_atr = atr.tail(20).mean()
    current_atr = atr.iloc[-1]
    
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    
    if current_atr > avg_atr * 1.5:
        return "HIGH_VOLATILITY"
    elif abs(sma20 - sma50) / sma50 > 0.05:
        return "TRENDING"
    else:
        return "RANGING"

def volume_analysis(volume: pd.Series, close: pd.Series) -> float:
    vol_trend = volume.pct_change(10).mean() * 100
    strength = max(0, min(100, 50 + vol_trend))
    return round(strength, 1)

def volume_price_trend(close: pd.Series, volume: pd.Series) -> pd.Series:
    price_change_pct = close.pct_change().fillna(0)
    vpt = (volume * price_change_pct).cumsum()
    return vpt

def chaikin_money_flow(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 21) -> pd.Series:
    mfv = ((close - low) - (high - close)) / (high - low) * volume
    mfv = mfv.replace([np.inf, -np.inf], 0).fillna(0)
    cmf = mfv.rolling(window).sum() / volume.rolling(window).sum()
    return cmf

def ease_of_movement(high: pd.Series, low: pd.Series, volume: pd.Series, window: int = 14) -> pd.Series:
    distance = ((high + low) / 2) - ((high.shift(1) + low.shift(1)) / 2)
    box_ratio = (volume / 100000000) / ((high - low).replace(0, 0.0001))
    emv = distance / box_ratio
    return emv.rolling(window).mean()

def volume_oscillator(volume: pd.Series, short_window: int = 5, long_window: int = 10) -> pd.Series:
    short_ma = volume.rolling(short_window).mean()
    long_ma = volume.rolling(long_window).mean()
    # Guard against division by zero when long_ma is 0
    return ((short_ma - long_ma) / long_ma.replace(0, np.nan)) * 100

def accumulation_distribution(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    mfm = ((close - low) - (high - close)) / (high - low)
    mfm = mfm.replace([np.inf, -np.inf], 0).fillna(0)
    mfv = mfm * volume
    ad = mfv.cumsum()
    return ad

def detect_market_maker_activity(close: pd.Series, volume: pd.Series, vpt: pd.Series, cmf: pd.Series, ad: pd.Series, vwap_deviation: float = 0, cumulative_delta: float = 0) -> dict:
    vol_ma20 = volume.rolling(20).mean()
    recent_vol = volume.tail(5).mean()
    avg_vol = vol_ma20.iloc[-1]
    
    price_ma20 = close.rolling(20).mean()
    price_trend = (close.iloc[-1] - price_ma20.iloc[-1]) / price_ma20.iloc[-1] * 100
    
    vpt_trend = vpt.iloc[-1] - vpt.iloc[-20] if len(vpt) > 20 else 0
    cmf_current = cmf.iloc[-1] if not pd.isna(cmf.iloc[-1]) else 0
    cmf_prev = cmf.iloc[-5:-1].mean() if len(cmf) > 5 else 0
    ad_trend = ad.iloc[-1] - ad.iloc[-20] if len(ad) > 20 else 0
    vol_spike = recent_vol > avg_vol * 1.5
    
    accumulation_signals = []
    distribution_signals = []
    
    if vol_spike and abs(price_trend) < 2: 
        accumulation_signals.append("VOL_SPIKE_STABLE")
        
    if vpt_trend > 0 and recent_vol > avg_vol * 0.8: 
        accumulation_signals.append("VPT_RISING")
        
    if cmf_current > 0.1 and cmf_current > cmf_prev: 
        accumulation_signals.append("CMF_POSITIVE")
        
    if ad_trend > 0: 
        accumulation_signals.append("AD_RISING")
        
    if vwap_deviation > 0 and cumulative_delta > 0: 
        accumulation_signals.append("VWAP_ABOVE_DELTA_POS")
    
    if vol_spike and price_trend < -1: 
        distribution_signals.append("VOL_SPIKE_DOWN")
        
    if vpt_trend < 0: 
        distribution_signals.append("VPT_FALLING")
        
    if cmf_current < -0.1 and cmf_current < cmf_prev: 
        distribution_signals.append("CMF_NEGATIVE")
        
    if ad_trend < 0: 
        distribution_signals.append("AD_FALLING")
        
    if vwap_deviation < 0 and cumulative_delta < 0: 
        distribution_signals.append("VWAP_BELOW_DELTA_NEG")
    
    acc_score = len(accumulation_signals)
    dist_score = len(distribution_signals)
    
    if acc_score > dist_score and acc_score >= 2:
        activity = "ACCUMULATION"
        confidence = min(90, acc_score * 20)
    elif dist_score > acc_score and dist_score >= 2:
        activity = "DISTRIBUTION"
        confidence = min(90, dist_score * 20)
    else:
        activity = "NEUTRAL"
        confidence = 50
    
    return {
        "activity": activity, 
        "confidence": int(confidence), 
        "accumulation_signals": accumulation_signals,
        "distribution_signals": distribution_signals, 
        "volume_spike": bool(vol_spike), 
        "vpt_trend": float(vpt_trend),
        "cmf_signal": float(cmf_current), 
        "ad_trend": float(ad_trend), 
        "vwap_deviation": float(vwap_deviation),
        "cumulative_delta": float(cumulative_delta)
    }

def estimate_market_maker_position(close: pd.Series, volume: pd.Series, mm_activity: dict, current_price: float, fundamentals: dict | None = None) -> dict:
    avg_daily_volume = volume.tail(30).mean() if len(volume) >= 30 else volume.mean()
    
    float_shares = 0
    shares_outstanding = 0
    float_estimated = False
    
    if fundamentals:
        float_shares = fundamentals.get("float_shares", 0) or 0
        shares_outstanding = fundamentals.get("shares_outstanding", 0) or 0
        float_estimated = fundamentals.get("float_estimated", False)

    if float_shares <= 0 and shares_outstanding > 0:
        float_shares = int(shares_outstanding * 0.25)
        float_estimated = True

    base_position_pct = 0.02
    if mm_activity["activity"] == "ACCUMULATION":
        base_position_pct = 0.025 
    elif mm_activity["activity"] == "DISTRIBUTION":
        base_position_pct = 0.015
    
    activity_multiplier = 1.0
    if mm_activity["activity"] == "ACCUMULATION": 
        activity_multiplier = 1.5 + (mm_activity["confidence"] - 50) / 100
    elif mm_activity["activity"] == "DISTRIBUTION": 
        activity_multiplier = 0.5 - (mm_activity["confidence"] - 50) / 200

    volume_multiplier = 1.0
    if mm_activity["volume_spike"]:
        volume_multiplier = 1.25
        
    volume_estimated_shares = int(avg_daily_volume * base_position_pct * activity_multiplier * volume_multiplier)

    if float_shares > 0:
        estimated_shares = max(volume_estimated_shares, int(float_shares * 0.005 * activity_multiplier))
    else:
        estimated_shares = volume_estimated_shares
        
    if shares_outstanding > 0 and estimated_shares > shares_outstanding: 
        estimated_shares = int(shares_outstanding * 0.05)
    
    position_value = estimated_shares * current_price
    
    float_base = float_shares if float_shares > 0 else max(int(avg_daily_volume * 250 * 0.4), 1)
    mm_float_pct = (estimated_shares / float_base) * 100 if float_base > 0 else 0

    accumulation_intensity = 0
    if mm_activity["activity"] == "ACCUMULATION": 
        accumulation_intensity = len(mm_activity["accumulation_signals"]) * mm_activity["confidence"] / 100
    elif mm_activity["activity"] == "DISTRIBUTION": 
        accumulation_intensity = -len(mm_activity["distribution_signals"]) * mm_activity["confidence"] / 100

    return {
        "estimated_shares": estimated_shares, 
        "position_value_idr": position_value, 
        "float_percentage": round(min(mm_float_pct, 20.0), 2),
        "float_shares": int(float_base) if float_base > 0 else 0, 
        "shares_outstanding": int(shares_outstanding), 
        "accumulation_intensity": accumulation_intensity,
        "volume_base": int(avg_daily_volume), 
        "confidence_adjusted": activity_multiplier, 
        "float_estimated": float_estimated,
    }

def estimate_retail_vs_mm_comparison(mm_position: dict, market_price: float, fundamentals: dict | None = None) -> dict:
    mm_shares = mm_position["estimated_shares"]
    
    float_shares = 0
    shares_outstanding = 0
    
    if fundamentals:
        float_shares = fundamentals.get("float_shares", 0) or 0
        shares_outstanding = fundamentals.get("shares_outstanding", 0) or 0

    if float_shares <= 0: 
        float_shares = mm_position.get("float_shares", 0) or 0
        
    if float_shares <= 0 and shares_outstanding > 0: 
        float_shares = int(shares_outstanding * 0.35)
        
    if float_shares <= 0: 
        float_shares = 100_000

    institutional_shares = int(float_shares * 0.35)
    
    if mm_shares > float_shares: 
        mm_shares = float_shares
        
    retail_shares = max(0, int(float_shares - institutional_shares - mm_shares))

    if retail_shares == 0:
        mm_vs_retail_ratio = 999.99
    else:
        mm_vs_retail_ratio = (mm_shares / retail_shares) * 100
        
    mm_vs_float_ratio = (mm_shares / float_shares * 100) if float_shares > 0 else 0

    if mm_vs_retail_ratio > 200: 
        dominance = "MM_DOMINANT"
    elif mm_vs_retail_ratio > 100: 
        dominance = "MM_STRONG"
    elif mm_vs_retail_ratio > 50: 
        dominance = "MM_MODERATE"
    elif mm_vs_retail_ratio > 25: 
        dominance = "BALANCED"
    else: 
        dominance = "RETAIL_DOMINANT"

    return {
        "retail_shares": retail_shares, 
        "retail_value_idr": retail_shares * market_price, 
        "institutional_shares": institutional_shares,
        "institutional_value_idr": institutional_shares * market_price, 
        "estimated_float_shares": float_shares, 
        "estimated_float_value": float_shares * market_price,
        "mm_vs_retail_ratio": round(mm_vs_retail_ratio, 2), 
        "mm_vs_float_ratio": round(mm_vs_float_ratio, 2), 
        "dominance": dominance,
    }

def fetch_fundamental_metrics(ticker: str) -> dict:
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

def detect_divergence(prices: pd.Series, rsi: pd.Series, lookback: int = 5) -> str:
    if len(prices) < lookback + 10 or len(rsi) < lookback + 10: 
        return "NONE"
        
    recent_price_min = prices.tail(lookback).min()
    prev_price_min = prices.iloc[-lookback-10:-lookback].min()
    
    recent_rsi_min = rsi.tail(lookback).min()
    prev_rsi_min = rsi.iloc[-lookback-10:-lookback].min()
    
    if recent_price_min < prev_price_min and recent_rsi_min > prev_rsi_min: 
        return "BULLISH_DIV"
    elif recent_price_min > prev_price_min and recent_rsi_min < prev_rsi_min: 
        return "BEARISH_DIV"
        
    return "NONE"

def calculate_ichimoku_cloud(high: pd.Series, low: pd.Series, close: pd.Series) -> dict:
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (high.rolling(52).max() + low.rolling(52).min()) / 2
    
    cloud_signal = "BULLISH" if close.iloc[-1] > senkou_a.iloc[-1] else "BEARISH"
    
    return {
        "tenkan": float(tenkan.iloc[-1]), 
        "kijun": float(kijun.iloc[-1]),
        "senkou_a": float(senkou_a.iloc[-1]), 
        "senkou_b": float(senkou_b.iloc[-1]),
        "signal": cloud_signal,
    }

def pattern_recognition(close: pd.Series, high: pd.Series, low: pd.Series, lookback: int = 5) -> str:
    if len(close) < max(lookback + 10, 4): 
        return "NONE"
        
    recent_high = high.tail(lookback).max()
    prev_high = high.iloc[-lookback-10:-lookback].max()
    resistance_broken = close.iloc[-1] > prev_high and close.iloc[-2] <= prev_high
    
    if resistance_broken: 
        return "BREAKOUT"
        
    if len(close) >= 3 and close.iloc[-1] > close.iloc[-2] * 1.02 and close.iloc[-2] < close.iloc[-3]: 
        return "REVERSAL"
        
    if len(close) >= 2 and close.iloc[-1] > close.iloc[-2::-1].min(): 
        return "CONTINUATION"
        
    return "NONE"

def backtest_signals(df: pd.DataFrame, lookback_periods: int = 252) -> dict:
    # NOTE:
    # The previous implementation constructed deterministic positive/negative
    # values for "returns" and then derived win-rate by counting sign, which
    # produced misleading 100% values. Here we provide a conservative,
    # deterministic *estimated* win-probability based on signal confidence and
    # RRR. This is not a full historical backtest but gives a meaningful
    # diagnostic number until a proper event-level backtester is implemented.
    if df.empty:
        return {}

    results = {
        "total_signals": 0,
        "accumulation_signals": 0,
        "distribution_signals": 0,
        "acc_win_rate": 0.0,
        "dist_win_rate": 0.0,
        "avg_return_acc": 0.0,
        "avg_return_dist": 0.0,
        "sharpe_acc": 0.0,
        "sharpe_dist": 0.0,
        "max_drawdown_acc": 0.0,
        "max_drawdown_dist": 0.0,
    }

    acc_probs = []
    dist_probs = []
    acc_expected = []
    dist_expected = []

    for _, row in df.iterrows():
        try:
            activity = row.get("MM_Activity", "")
            conf = float(row.get("MM_Confidence", 0) or 0)
            rrr = float(row.get("RRR", 0) or 0)
        except Exception:
            continue

        # Only consider high-confidence signals for these summary stats
        if activity == "ACCUMULATION" and conf >= 75:
            # Heuristic: base probability plus confidence and modest uplift from RRR
            prob = 0.20 + (conf - 50) / 100.0 * 0.5 + min(rrr / 4.0, 0.25)
            prob = max(0.05, min(0.95, prob))
            acc_probs.append(prob)
            acc_expected.append((rrr / 100.0) * (conf / 100.0))

        if activity == "DISTRIBUTION" and conf >= 75:
            # Shorting/distribution has a slightly different shape
            prob = 0.18 + (conf - 50) / 100.0 * 0.45 + min(rrr / 5.0, 0.20)
            prob = max(0.05, min(0.95, prob))
            dist_probs.append(prob)
            dist_expected.append(-(rrr / 100.0) * (conf / 100.0))

    if acc_probs:
        results["accumulation_signals"] = len(acc_probs)
        results["acc_win_rate"] = float(np.mean(acc_probs))
        results["avg_return_acc"] = float(np.mean(acc_expected)) if acc_expected else 0.0
        results["sharpe_acc"] = (np.mean(acc_expected) / np.std(acc_expected)) if len(acc_expected) > 1 and np.std(acc_expected) > 0 else 0.0
        results["max_drawdown_acc"] = float(np.min(acc_expected)) if acc_expected else 0.0

    if dist_probs:
        results["distribution_signals"] = len(dist_probs)
        results["dist_win_rate"] = float(np.mean(dist_probs))
        results["avg_return_dist"] = float(np.mean(dist_expected)) if dist_expected else 0.0
        results["sharpe_dist"] = (np.mean(dist_expected) / np.std(dist_expected)) if len(dist_expected) > 1 and np.std(dist_expected) > 0 else 0.0
        results["max_drawdown_dist"] = float(np.min(dist_expected)) if dist_expected else 0.0

    results["total_signals"] = results["accumulation_signals"] + results["distribution_signals"]
    return results

def fetch_news_sentiment(ticker: str) -> dict:
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

        # FIX: Gunakan VADER (ringan, sudah terinstall, tidak perlu download 499MB)
        # daripada get_ai_model().analyze_sentiment() yang trigger download PyTorch model.
        if SENTIMENT_AVAILABLE:
            analyzer = SentimentIntensityAnalyzer()
            def _score_title(text: str) -> float:
                return analyzer.polarity_scores(text)["compound"]
        else:
            # Fallback keyword sederhana jika VADER tidak ada
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

def build_covariance_matrix(candidates: pd.DataFrame) -> np.ndarray:
    returns_list = []
    tickers = []
    
    for ticker in candidates["Ticker"].tolist():
        ticker_full = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
        data = fetch_price_data(ticker_full, period="1y", interval="1d")
        if data.empty or "Close" not in data.columns:
            continue
            
        close = data["Close"].squeeze()
        pct_returns = close.pct_change().dropna()
        if len(pct_returns) >= 20:
            returns_list.append(pct_returns.iloc[-60:])
            tickers.append(ticker)

    if len(returns_list) < len(candidates) or len(returns_list) < 2:
        return np.eye(len(candidates)) * 0.2

    returns_df = pd.concat(returns_list, axis=1, join="inner")
    returns_df.columns = tickers[: returns_df.shape[1]]
    cov_matrix = returns_df.cov().fillna(0).values
    
    if cov_matrix.shape[0] != len(candidates):
        cov_matrix = np.eye(len(candidates)) * 0.2
        
    return cov_matrix

def optimize_portfolio(df: pd.DataFrame, risk_free_rate: float = 0.05) -> dict:
    if not OPTIMIZATION_AVAILABLE or df.empty: 
        return {"error": "Optimization not available or no data"}
        
    candidates = df[(df["MM_Activity"] == "ACCUMULATION") & (df["MM_Confidence"] >= 75)].head(10)
    if len(candidates) < 3: 
        return {"error": "Not enough high-confidence accumulation signals"}
    
    expected_returns = []
    for _, row in candidates.iterrows():
        exp_return = (row["RRR"] / 100) * (row["MM_Confidence"] / 100)
        expected_returns.append(exp_return)
        
    expected_returns = np.array(expected_returns)
    n_assets = len(candidates)
    cov_matrix = build_covariance_matrix(candidates)
    
    if cov_matrix.shape != (n_assets, n_assets):
        cov_matrix = np.eye(n_assets) * 0.2
    
    constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
    bounds = tuple((0, 1) for _ in range(n_assets))
    
    def neg_sharpe_ratio(weights): 
        portfolio_return = np.dot(weights, expected_returns)
        portfolio_volatility = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
        if portfolio_volatility == 0:
            return 0.0
        sharpe = (portfolio_return - risk_free_rate) / portfolio_volatility
        return -sharpe
    
    initial_weights = np.ones(n_assets) / n_assets
    result = minimize(neg_sharpe_ratio, initial_weights, method='SLSQP', bounds=bounds, constraints=constraints)
    
    if result.success:
        optimal_weights = result.x
        tickers = candidates["Ticker"].tolist()
        return {
            "optimal_weights": dict(zip(tickers, optimal_weights)), 
            "expected_portfolio_return": np.dot(optimal_weights, expected_returns), 
            "portfolio_volatility": np.sqrt(np.dot(optimal_weights.T, np.dot(cov_matrix, optimal_weights))), 
            "sharpe_ratio": -result.fun
        }
    else:
        return {"error": "Optimization failed"}

def send_email_alert(subject: str, body: str, to_email: str, from_email: str = "screener@alert.com"):
    if not ALERTS_AVAILABLE: 
        print(f"Email alert: {subject} - {body}")
        return
        
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        print("Email alert sent successfully")
    except Exception as e: 
        print(f"Failed to send email: {e}")

def check_and_alert(df: pd.DataFrame, email: str = None):
    high_conf_signals = df[
        ((df["MM_Activity"] == "ACCUMULATION") & (df["MM_Confidence"] >= 80)) |
        ((df["MM_Activity"] == "DISTRIBUTION") & (df["MM_Confidence"] >= 80))
    ]
    
    if not high_conf_signals.empty:
        alert_body = "High-confidence Market Maker signals detected:\n\n"
        for _, row in high_conf_signals.iterrows():
            alert_body += f"{row['Ticker']}: {row['MM_Activity']} ({row['MM_Confidence']}%) - Dominance: {row['Dominance']}\n"
        
        if email:
            send_email_alert("Market Maker Alert", alert_body, email)
        else:
            print(f"{C.BOLD}{C.YELLOW}ALERT: {alert_body}{C.RESET}")

def position_size_calc(account_equity: float, risk_pct: float, entry: float, stop_loss: float) -> dict:
    risk_amount = account_equity * (risk_pct / 100)
    points_at_risk = entry - stop_loss
    
    if points_at_risk <= 0:
        return {"shares": 0, "position_size": 0, "risk_amount": 0, "risk_per_share": 0}
        
    shares = int(risk_amount / points_at_risk)
    position_value = shares * entry
    
    return {
        "shares": shares,
        "position_size": int(position_value),
        "risk_amount": int(risk_amount),
        "risk_per_share": float(points_at_risk),
    }

def kirim_notifikasi_discord(df: pd.DataFrame, webhook_url: str):
    """Mengirim hasil screener ke Discord menggunakan Webhook dengan format Embed dan Estimasi Waktu."""
    import requests
    import datetime
    
    # 👇 TAMBAHKAN 3 BARIS INI 👇
    if not webhook_url or not webhook_url.startswith("http"):
        print("  [Discord] URL Webhook kosong. Notifikasi Discord dilewati.")
        return
    # 👆 --------------------- 👆
    # Hanya ambil saham yang sinyalnya kuat agar Discord tidak spam
    df_alert = df[df["Sinyal"].isin(["ULTRA_BUY", "STRONG_BUY", "BUY"])]
    
    if df_alert.empty:
        print("  [Discord] Tidak ada sinyal kuat hari ini, notifikasi dilewati.")
        return

    embeds = []
    for _, row in df_alert.iterrows():
        # Warna hijau terang untuk ULTRA BUY, hijau tua untuk STRONG BUY
        warna = 0x00FF00 if row["Sinyal"] == "ULTRA_BUY" else 0x00AA00
        
        # Info AI
        ai_info = f"🤖 {row.get('AI_Win_Prob%', 0)}% Win Rate\n{row.get('AI_Verdict', 'N/A')}" if row.get('AI_Win_Prob%', 0) > 0 else "Tidak ada Prediksi AI"
        
        # ─── LOGIKA ESTIMASI WAKTU (TIME TO TP/SL) ───
        if row['Regime'] == "HIGH_VOLATILITY":
            estimasi_waktu = "⏳ **1 - 3 Hari Bursa** (Fast Trade / Volatil)"
        elif row['Regime'] == "TRENDING":
            estimasi_waktu = "⏳ **3 - 5 Hari Bursa** (Swing Trend Stable)"
        else:
            estimasi_waktu = "⏳ **5 - 10 Hari Bursa** (Swing Range / Sabar)"

        # Membuat Embed Pesan Discord
        embed = {
            "title": f"🚨 {row['Sinyal'].replace('_', ' ')}: {row['Ticker']}",
            "color": warna,
            "fields": [
                {"name": "Harga Entry", "value": f"Rp {row['Harga']:,}", "inline": True},
                {"name": "Target (TP)", "value": f"Rp {row['Target_1']:,}", "inline": True},
                {"name": "Stop Loss", "value": f"Rp {row['Stop_Loss']:,}", "inline": True},
                {"name": "Skor Teknikal", "value": f"⭐ {row['Skor']}/15\n⚖️ RRR: {row['RRR']}", "inline": True},
                {"name": "Prediksi AI", "value": ai_info, "inline": True},
                # BARIS BARU UNTUK ESTIMASI WAKTU
                {"name": "Perkiraan Waktu Hold", "value": estimasi_waktu, "inline": False},
                {"name": "Market Maker", "value": f"🐋 {row['MM_Activity']} ({row['MM_Confidence']}%)", "inline": True},
                # Tambahkan baris ini:
                {"name": "Foreign Flow (Asing)", "value": f"🌍 {row.get('Foreign_Status', 'N/A')}", "inline": True},
                # FIX BUG 5: duplikat field "Prediksi AI" dihapus
                
            ],
            "footer": {"text": f"IHSG Quant Screener v7.0 • {datetime.datetime.now().strftime('%d %b %Y')}"}
        }
        embeds.append(embed)

        # Discord membatasi maksimal 10 embed per 1 kali pengiriman
        if len(embeds) == 10:
            requests.post(webhook_url, json={"content": "📈 **UPDATE SAHAM POTENSIAL HARI INI**", "embeds": embeds})
            embeds = []

    # Kirim sisa embed jika ada
    if embeds:
        response = requests.post(webhook_url, json={"content": "📈 **UPDATE SAHAM POTENSIAL HARI INI**", "embeds": embeds})
        if response.status_code == 204 or response.status_code == 200:
            print(f"  {C.GREEN}✓ Notifikasi & Estimasi Waktu berhasil dikirim ke Discord Automaton!{C.RESET}")
        else:
            print(f"  {C.RED}✗ Gagal mengirim ke Discord: HTTP {response.status_code}{C.RESET}")

# ==========================================
# 🛡️ DATA SANITIZER (ANTI-GARBAGE FILTER)
# ==========================================
def validasi_data_yfinance(df: pd.DataFrame, ticker: str) -> bool:
    """Membuang data cacat dari yfinance sebelum merusak kalkulasi AI"""
    if df.empty or len(df) < 50:
        return False
        
    # 1. Deteksi Saham Tidur (Suspend / Gocap)
    # Jika volume 10 hari terakhir 0 terus, lewati.
    if df['Volume'].tail(10).sum() == 0:
        return False
        
    # 2. Deteksi Anomali Stock Split/Right Issue yang belum di-adjust
    # Batas Auto Reject Atas (ARA) IHSG adalah 35%. 
    # Jika harga loncat > 40% sehari, 99% itu data cacat dari YF.
    pct_change = df['Close'].tail(5).pct_change().abs() * 100
    anomali = pct_change[pct_change > 40.0]
    
    if not anomali.empty:
        logger.warning("[SANITIZER] %s dilewati — anomali data ekstrem terdeteksi (kemungkinan cacat YF).", ticker)
        return False
        
    return True

# ─── Fungsi Utama Analisis v5.0 (FULL LENGTH) ────────────────────────────────
def analisis_saham(ticker: str) -> dict | None:
    """
    Upgraded analysis dengan 12 indikator + multi-timeframe + regime detection + AI.
    Skor maksimal = 15 poin.
    """
    try:
        global global_total_discan, global_saham_uptrend
        ticker = ticker.strip().upper()
        if "." not in ticker:
            ticker = f"{ticker}.JK"
        
        # Memanggil fetcher yang sudah kebal blokir
        data_daily = fetch_price_data(ticker, period="6mo", interval="1d")
        
        # PASANG SATPAM DI SINI:
        if not validasi_data_yfinance(data_daily, ticker):
            return None

        close = data_daily["Close"].squeeze()
        high = data_daily["High"].squeeze()
        low = data_daily["Low"].squeeze()
        volume = data_daily["Volume"].squeeze()
        # 🔥 Tambahan untuk Z-Score Anomaly:
        open_series = data_daily["Open"].squeeze()
        open_price = float(open_series.iloc[-1])
        ema21 = calculate_ema(close, 21)
        ema50 = calculate_ema(close, 50)
        hma20 = hma(close, 20)
        # 🔥 Update Sensor Market Breadth
        with _counter_lock:
            global_total_discan += 1
            if float(close.iloc[-1]) > float(ema50.iloc[-1]):
                global_saham_uptrend += 1
        adx_val = calculate_adx(high, low, close)

        macd_line, macd_signal, macd_hist = calculate_macd(close)

        rsi = calculate_rsi(close)

        # FIX BUG 3: stoch_k & stoch_d dipakai tapi tidak pernah dihitung
        stoch_indicator = StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
        stoch_k = stoch_indicator.stoch()
        stoch_d = stoch_indicator.stoch_signal()

        bb_mid, bb_up, bb_low = calculate_bollinger_bands(close)

        vol_sma20 = calculate_sma(volume, 20)

        atr = calculate_atr(high, low, close)

        obv = OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume()
        # ... (kode perhitungan indikator teknikal di atasnya) ...
        
        fundamentals = fetch_fundamental_metrics(ticker)
        sentiment = fetch_news_sentiment(ticker)

        # ── Phase-3: Missing fundamental defaults ──────────────────────────
        # yfinance sering tidak punya PE/PBV untuk saham IHSG kecil.
        # Jika None/0, gunakan nilai konservatif agar scoring tidak crash.
        _raw_pe  = fundamentals.get("trailing_pe", 0) or 0
        _raw_bv  = fundamentals.get("book_value",  0) or 0
        if _raw_pe == 0:
            logger.warning("[FUNDAMENTAL] %s: PE tidak tersedia — pakai default 15", ticker)
            fundamentals["trailing_pe"] = 15.0   # default konservatif
        if _raw_bv == 0:
            logger.warning("[FUNDAMENTAL] %s: Book Value tidak tersedia — pakai default 1.5", ticker)
            fundamentals["book_value"] = 1.0     # proxy agar pbv_val = price/1 → dihitung wajar

        # 🔥 Taruh Sensor Asing di sini, berkumpul dengan data eksternal lain
        foreign_data = fetch_foreign_flow(ticker)
        # Panggil fungsi dari file broker_scraper.py
        data_broksum = analisis_broksum(ticker)
        # 🔥 Panggil Senjata Baru v9.0
        vol_zscore = detect_zscore_anomaly(volume)
        sentimen_lokal = fetch_berita_lokal(ticker)
        bulan_sekarang = datetime.date.today().month  # Untuk Time Encoding

        price = float(close.iloc[-1])
        # Deklarasi awal 'vwap' agar terhindar dari UnboundLocalError
        vwap = close 
        try:
            vwap = VolumeWeightedAveragePrice(high=high, low=low, close=close, volume=volume).volume_weighted_average_price()
            vwap_v = float(vwap.iloc[-1])
        except:
            vwap_v = float(close.iloc[-1]) 

        vpt = volume_price_trend(close, volume)
        cmf = chaikin_money_flow(high, low, close, volume, window=21)
        emv = ease_of_movement(high, low, volume, window=14)
        vol_osc = volume_oscillator(volume, short_window=5, long_window=10)
        ad_line = accumulation_distribution(high, low, close, volume)
        
        # FIX BUG 6: fundamentals & sentiment di-fetch dua kali — hapus duplikat ini
        # (sudah di-fetch di lines 1149-1150 di atas)

        vwap_deviation = (price - vwap_v) / vwap_v * 100 if vwap_v > 0 else 0
        tick_volume = volume.pct_change().fillna(0)
        cumulative_delta = (tick_volume * (close - close.shift(1)).fillna(0)).cumsum().iloc[-1]
        
        mm_activity = detect_market_maker_activity(close, volume, vpt, cmf, ad_line, vwap_deviation, cumulative_delta)
        
        mm_position = estimate_market_maker_position(close, volume, mm_activity, price, fundamentals=fundamentals)
        
        retail_comparison = estimate_retail_vs_mm_comparison(mm_position, price, fundamentals=fundamentals)
        
        ema21_val = float(ema21.iloc[-1])
        ema50_val = float(ema50.iloc[-1])
        hma_val = float(hma20.iloc[-1])
        adx_v = float(adx_val.iloc[-1])
        macd_v = float(macd_line.iloc[-1])
        macd_s = float(macd_signal.iloc[-1])
        macd_h = float(macd_hist.iloc[-1])
        rsi_v = float(rsi.iloc[-1])
        stoch_v = float(stoch_k.iloc[-1])
        stoch_d_v = float(stoch_d.iloc[-1])
        bb_mid_v = float(bb_mid.iloc[-1])
        bb_up_v = float(bb_up.iloc[-1])
        bb_low_v = float(bb_low.iloc[-1])
        vol_v = float(volume.iloc[-1])
        vol_sma_v = float(vol_sma20.iloc[-1])
        atr_v = float(atr.iloc[-1])
        obv_v = float(obv.iloc[-1])
        obv_ma = float(obv.rolling(20).mean().iloc[-1])
        
        support, resistance = detect_support_resistance(close, lookback=20)
        regime = market_regime(close, atr)
        vol_strength = volume_analysis(volume, close)

        divergence_signal = detect_divergence(close, rsi, lookback=5)
        
        ichimoku_data = calculate_ichimoku_cloud(high, low, close)
        
        pattern = pattern_recognition(close, high, low, lookback=5)
        
        sma_tp = (high + low + close) / 3
        tp_mean = sma_tp.rolling(20).mean()
        tp_mean_dev = sma_tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
        cci_series = (sma_tp - tp_mean) / (0.015 * tp_mean_dev)
        cci_v = float(cci_series.iloc[-1]) if not cci_series.empty and np.isfinite(cci_series.iloc[-1]) else 0.0
        
        vwap_signal = "ABOVE" if price > vwap_v else "BELOW"
        vwap_slope = float(vwap.iloc[-1] - vwap.iloc[-5]) if len(vwap) >= 5 else 0.0
        vwap_trend = vwap_slope > 0
        
        kc_high = bb_mid_v + (2.0 * atr_v)
        kc_low = bb_mid_v - (2.0 * atr_v)
        kc_signal = "NEAR_TOP" if price > (kc_high + kc_low) / 2 else "NEAR_BOTTOM"

        macd_hist_delta = float(macd_hist.iloc[-1] - macd_hist.iloc[-3]) if len(macd_hist) >= 3 else 0.0
        obv_trend = obv_v - obv_ma
        breakout_strength = price > resistance and vol_v > vol_sma_v * 1.3

        # ── Phase-2: NEW engineered features ─────────────────────────────────
        # 1. RSI × Volume interaction (momentum quality filter)
        #    Tinggi = RSI kuat + volume besar → sinyal lebih valid
        rsi_vol_interaction = float(rsi_v * vol_strength)

        # 2. Rolling 20-day realised volatility (annualised)
        #    Dipakai sebagai input AI dan risk-adjusted scoring
        rolling_vol_20 = float(close.pct_change().rolling(20).std().iloc[-1])
        if np.isnan(rolling_vol_20) or np.isinf(rolling_vol_20):
            rolling_vol_20 = 0.0

        # 3. Sector / IHSG correlation (20-day rolling Pearson)
        #    Mengukur seberapa erat saham mengikuti IHSG; rendah = alpha story
        sector_corr = 0.0
        try:
            if len(ihsg_data) >= 20 and len(close) >= 20:
                stock_ret  = close.pct_change().dropna().tail(60)
                ihsg_ret   = ihsg_data.pct_change().dropna()
                # align by index position (both daily)
                min_len    = min(len(stock_ret), len(ihsg_ret))
                if min_len >= 20:
                    corr_val   = float(np.corrcoef(
                        stock_ret.values[-min_len:],
                        ihsg_ret.values[-min_len:]
                    )[0, 1])
                    sector_corr = 0.0 if np.isnan(corr_val) else round(corr_val, 4)
        except Exception as _corr_err:
            logger.debug("[%s] sector_corr calc failed: %s", ticker, _corr_err)

        # Fill any NaN from all feature variables before AI inference
        # (centralised guard — handles edge cases in short series)
        _features_guard = {
            "rsi_vol_interaction": rsi_vol_interaction,
            "rolling_vol_20":      rolling_vol_20,
            "sector_corr":         sector_corr,
        }
        for _k, _v in _features_guard.items():
            if np.isnan(_v) or np.isinf(_v):
                _features_guard[_k] = 0.0
        rsi_vol_interaction = _features_guard["rsi_vol_interaction"]
        rolling_vol_20      = _features_guard["rolling_vol_20"]
        sector_corr         = _features_guard["sector_corr"]
        # ── end Phase-2 new features ──────────────────────────────────────────
        
        sentiment_bonus_score = 0.0
        if sentiment.get("news_count", 0) >= 2:
            if sentiment["sentiment_score"] > 0.25:
                sentiment_bonus_score = 1.0
            elif sentiment["sentiment_score"] > 0.1:
                sentiment_bonus_score = 0.5

        weekly_bullish = monthly_bullish = False
        try:
            data_w = fetch_price_data(ticker, period="1y", interval="1wk")
            if not data_w.empty and len(data_w) >= 20:
                close_w = data_w["Close"].squeeze()
                ema20_w = EMAIndicator(close=close_w, window=20).ema_indicator()
                weekly_bullish = float(close_w.iloc[-1]) > float(ema20_w.iloc[-1])

            data_m = fetch_price_data(ticker, period="2y", interval="1mo")
            if not data_m.empty and len(data_m) >= 12:
                close_m = data_m["Close"].squeeze()
                ema12_m = EMAIndicator(close=close_m, window=12).ema_indicator()
                monthly_bullish = float(close_m.iloc[-1]) > float(ema12_m.iloc[-1])
        except:
            pass

        # ── SISTEM SCORING BERBOBOT v3.0 (GAYA VERTIKAL ASLI) ───────────────────
        # --- OPTIMASI 1: FILTER UANG BEREDAR (TURNOVER) ---
        # Menghitung rata-rata nilai transaksi harian (Volume x Harga) selama 20 hari
        turnover_harian = close * volume
        avg_turnover = float(turnover_harian.tail(20).mean())

        # ── SISTEM SCORING BERBOBOT v5.1 (GAYA VERTIKAL ASLI) ───────────────────
        # ── SISTEM SCORING BERBOBOT v5.1 (DYNAMIC CONFIG) ───────────────────
        skor = 0 + MACRO_PENALTY
        konfirmasi = []
        
        if MACRO_PENALTY < 0:
            konfirmasi.append("Macro_Defensive-")
        elif MACRO_PENALTY > 0:
            konfirmasi.append("Macro_Aggressive+")
            
        # --- OPTIMASI ROTASI SEKTOR ---
        sektor_saham = PETA_SEKTOR.get(ticker, "-")
        momentum_sektor = SEKTOR_MOMENTUM.get(sektor_saham, 0.0)
        
        if momentum_sektor > 1.5:
            skor += BOBOT_SKOR["Sector_Leadership"] # Diubah dari Sector_HOT
            konfirmasi.append("Sector_HOT**")
        elif momentum_sektor < -1.5:
            skor += BOBOT_SKOR["Sector_Cold"]       # Diubah dari Sector_COLD
            konfirmasi.append("Sector_COLD-")

        # --- OPTIMASI 2: ANTI KEJAR PUCUK (PULLBACK LOGIC) ---
        if price > bb_up_v:
            skor += BOBOT_SKOR["Overbought"]
            konfirmasi.append("Overbought-")

        # --- OPTIMASI 3: FILTER FUNDAMENTAL (VALUASI PER & PBV) ---
        per_val = fundamentals.get("trailing_pe", 0)
        book_val = fundamentals.get("book_value", 0)
        pbv_val = (price / book_val) if book_val > 0 else 0

        if per_val > 0 and per_val <= 15:
            skor += BOBOT_SKOR["PER_Cheap"]         # Diubah dari PER_Murah
            konfirmasi.append("PER_Murah*")
        elif per_val < 0:
            skor += BOBOT_SKOR["EPS_Minus"]
            konfirmasi.append("EPS_Minus-")
            
        if 0 < pbv_val <= 1.5:
            skor += BOBOT_SKOR["PBV_Strong"]        # Diubah dari PBV_Murah
            konfirmasi.append("PBV_Murah")
        elif pbv_val > 5.0:
            skor += BOBOT_SKOR["PBV_Mahal"]
            konfirmasi.append("PBV_Mahal-")

        # --- OPTIMASI 4: KEKUATAN RELATIF (ALPHA LEADER) vs IHSG ---
        try:
            if len(close) >= 20 and len(ihsg_data) >= 20:
                stock_ret_20 = (price - float(close.iloc[-20])) / float(close.iloc[-20]) * 100
                ihsg_ret_20 = (float(ihsg_data.iloc[-1]) - float(ihsg_data.iloc[-20])) / float(ihsg_data.iloc[-20]) * 100
                
                if stock_ret_20 > ihsg_ret_20 + 5: 
                    skor += BOBOT_SKOR["RS_Outperform"] # Diubah dari RS_Strong
                    konfirmasi.append("RS_Strong*")
                
                if ihsg_ret_20 < 0 and stock_ret_20 > 0: 
                    skor += BOBOT_SKOR["Alpha_Leader"]
                    konfirmasi.append("Alpha_Leader***")
        except: pass

        # --- OPTIMASI 5: DETEKSI VCP (VOLATILITY CONTRACTION) ---
        highest_20 = float(high.tail(20).max())
        jarak_pucuk = (highest_20 - price) / price * 100
        atr_20_avg = float(atr.tail(20).mean())
        
        if 0 <= jarak_pucuk <= 4 and atr_v < atr_20_avg and vol_v < vol_sma_v * 0.7:
            skor += BOBOT_SKOR["VCP_Pattern"]          # Diubah dari VCP_Setup
            konfirmasi.append("VCP_Setup**")

        # --- OPTIMASI 6: WYCKOFF ABSORPTION (EFFORT VS RESULT) ---
        spread = float(high.iloc[-1] - low.iloc[-1])
        if vol_v > vol_sma_v * 1.5 and spread < atr_v * 0.8:
            if close.iloc[-1] >= (high.iloc[-1] + low.iloc[-1]) / 2: 
                skor += BOBOT_SKOR["Wyckoff_Absorb"]
                konfirmasi.append("Wyckoff_Absorb***")

        # --- OPTIMASI 7: NLP WEB SCRAPER SENTIMEN BERITA ---
        try:
            sent_score, sent_label = get_sentiment(ticker)
            if sent_label == "BULLISH":
                skor += BOBOT_SKOR["News_BULLISH"]
                konfirmasi.append("News_BULLISH***")
            elif sent_label == "BEARISH":
                skor += BOBOT_SKOR["News_BEARISH"]
                konfirmasi.append("News_BEARISH---")
        except:
            pass

        # --- OPTIMASI 8: FOREIGN FLOW (ARUS DANA ASING) ---
        if foreign_data["foreign_status"] == "ACCUMULATION":
            skor += BOBOT_SKOR["Foreign_Buy"]
            konfirmasi.append("Foreign_Buy**")
        elif foreign_data["foreign_status"] == "DISTRIBUTION":
            skor += BOBOT_SKOR["Foreign_Sell"]
            konfirmasi.append("Foreign_Sell-")

        jarak_ke_ema21 = (price - ema21_val) / ema21_val
        if 0 < jarak_ke_ema21 < 0.03: 
            skor += BOBOT_SKOR["Pullback_EMA21"]
            konfirmasi.append("Pullback_EMA21**")

        # --- OPTIMASI 9: ANOMALI BANDAR (Z-SCORE) ---
        if vol_zscore >= 3.0 and close.iloc[-1] > open_price: 
            skor += BOBOT_SKOR["Vol_Anomaly"]
            konfirmasi.append("VOL_ANOMALY***")
        
        # --- OPTIMASI 10: BASIC TECHNICALS (RESTORED) ---
        if price > ema21_val > ema50_val and price > hma_val:
            skor += 2.0
            konfirmasi.append("EMA+HMA**")
        elif price > ema21_val > ema50_val:
            skor += 1.5
            konfirmasi.append("EMA*")

        # --- OPTIMASI 11: BROKER SUMMARY (REAL BANDARMOLOGI) ---
        if data_broksum["status_bandar"] == "BIG_ACCUMULATION":
            skor += BOBOT_SKOR.get("Broksum_ACCUM", 2.0)
            konfirmasi.append("Broksum_ACCUM***")
        elif data_broksum["status_bandar"] == "DISTRIBUTION":
            skor += BOBOT_SKOR.get("Broksum_DIST", -1.5)
            konfirmasi.append("Broksum_DIST-")

        if adx_v > 40: skor += 1.5
        elif adx_v > 30: skor += 1.2

        if macd_v > macd_s and macd_h > 0 and obv_v > obv_ma: skor += 1.5
        elif macd_v > macd_s and macd_h > 0: skor += 1.0

        if 40 <= rsi_v <= 65: skor += 1.0
        if stoch_v < 80 and stoch_v > stoch_d_v: skor += 1.0
        
        if price > bb_mid_v and price > support: skor += 1.0
        if vol_v > vol_sma_v * 1.2 and obv_v > obv_ma: skor += 1.0

        if breakout_strength:
            skor += 0.7
            konfirmasi.append("Breakout+")

        skor = min(skor, 15.0)

        # ── Manajemen Risiko (UPGRADED) ────────────────────────────────────────
        risk_factor = 1.5 if regime == "HIGH_VOLATILITY" else 1.2
        stop_loss = round(price - (risk_factor * atr_v))
        target_1 = round(price + (2.0 * atr_v))
        target_2 = round(price + (3.5 * atr_v))
        target_3 = round(price + (5.0 * atr_v))

        risk_pct = round(((price - stop_loss) / price) * 100, 1)
        reward_pct = round(((target_1 - price) / price) * 100, 1)
        rrr = round(reward_pct / risk_pct, 2) if risk_pct != 0 else 0

        # ─── COMPONENT-BASED CONFIDENCE CALCULATION v10 (BALANCED) ───
        # Calculate each tier independently (0-100), then weight them
        
        # TECHNICAL COMPONENT (35% weight)
        tech_score = 0
        if price > ema21_val > ema50_val and price > hma_val:
            tech_score += 20
        if 30 <= rsi_v <= 50:  # Good entry RSI
            tech_score += 15
        if macd_h > 0 and macd_h > macd_hist.iloc[-2] if len(macd_hist) > 1 else False:
            tech_score += 15
        if vol_v > vol_sma_v * 1.5:
            tech_score += 15
        if adx_v > 35:
            tech_score += 20
        if pattern in ["BREAKOUT", "REVERSAL"]:
            tech_score += 15
        tech_score = min(100, tech_score)
        
        # FUNDAMENTAL COMPONENT (25% weight)
        fund_score = 0
        if 0 < per_val <= 12:
            fund_score += 25
        elif 12 < per_val <= 18:
            fund_score += 15
        if 0 < pbv_val <= 1.0:
            fund_score += 25
        elif pbv_val > 5.0:
            fund_score -= 20
        if fundamentals.get("earnings_growth", 0) > 0:
            fund_score += 15
        if fundamentals.get("dividend_yield", 0) > 3:
            fund_score += 10
        fund_score = min(100, max(-30, fund_score))  # Allow negatives
        
        # RELATIVE STRENGTH COMPONENT (20% weight)
        rs_score = 0
        try:
            if len(close) >= 20 and len(ihsg_data) >= 20:
                stock_ret_20 = (price - float(close.iloc[-20])) / float(close.iloc[-20]) * 100
                ihsg_ret_20 = (float(ihsg_data.iloc[-1]) - float(ihsg_data.iloc[-20])) / float(ihsg_data.iloc[-20]) * 100
                if stock_ret_20 > ihsg_ret_20 + 5:
                    rs_score += 30
                if stock_ret_20 > ihsg_ret_20:
                    rs_score += 20
        except:
            pass
        if momentum_sektor > 1.5:
            rs_score += 25
        rs_score = min(100, rs_score)
        
        # SENTIMENT COMPONENT (20% weight)
        sent_score = 0
        if sentiment.get("sentiment_score", 0) > 0.25 and sentiment.get("news_count", 0) >= 2:
            sent_score += 30
        elif sentiment.get("sentiment_score", 0) > 0.1 and sentiment.get("news_count", 0) >= 2:
            sent_score += 15
        elif sentiment.get("sentiment_score", 0) < -0.2 and sentiment.get("news_count", 0) >= 2:
            sent_score -= 30  # SYMMETRIC
        if foreign_data["foreign_status"] == "ACCUMULATION":
            sent_score += 20
        elif foreign_data["foreign_status"] == "DISTRIBUTION":
            sent_score -= 25
        if mm_activity["activity"] == "ACCUMULATION":
            sent_score += 15
        sent_score = min(100, max(-50, sent_score))
        
        # WEIGHTED CONFIDENCE (Final Score)
        # REVISI: Bobot Technical dibesarkan (60%) karena data fundamental YF sering kosong untuk IHSG
        confidence = (
            tech_score * 0.60 +          
            max(0, fund_score) * 0.10 +  
            rs_score * 0.20 +            
            max(0, sent_score) * 0.10    
        )
        confidence = min(100, max(0, confidence))

        bb_width = round(((bb_up_v - bb_low_v) / bb_mid_v) * 100, 1)

        # ── Penentuan Sinyal v10 (BALANCED SCORING + MACRO) ──────────────────────
        sinyal = "HINDARI"
        signal_strength = "F"
        
        # Thresholds yang disesuaikan dengan sistem confidence baru
        confidence_threshold_strong = 75 if IHSG_TREND == "UP" else 80
        confidence_threshold_buy = 55 if IHSG_TREND == "UP" else 65
        
        # Safety filter: DIMATIKAN SEMENTARA (Karena data Volume/Turnover YF sering ngaco)
        has_critical_risk = (
            per_val < -50 or                                # Toleransi minus ekstrem untuk saham tech
            pbv_val > 50 or                                 # Toleransi PBV ekstrem
            fundamentals.get("bankruptcy_risk", 0) > 0.5   
        )
        
        if has_critical_risk:
            sinyal = "HINDARI"
            signal_strength = "RISK"
        # TIER 1: ULTRA_BUY (A+ / A) - Confluence of everything
        elif confidence >= 85 and skor >= 10 and rrr >= 1.8 and weekly_bullish and IHSG_TREND == "UP":
            sinyal = "ULTRA_BUY"
            signal_strength = "A+"
        elif confidence >= 80 and skor >= 9.5 and rrr >= 1.6 and weekly_bullish:
            sinyal = "ULTRA_BUY"
            signal_strength = "A"
        # TIER 2: STRONG_BUY (B+ / B) - High confidence + good trend
        elif confidence >= confidence_threshold_strong and skor >= 8.0: # Skor dilonggarkan sedikit
            sinyal = "STRONG_BUY"
            signal_strength = "B+"
        elif confidence >= 70 and skor >= 7.0:                          # Diturunkan dari 75
            sinyal = "STRONG_BUY"
            signal_strength = "B"
        # TIER 3: BUY (C+ / C) - Moderate confidence, tradable setup
        elif confidence >= 50 and skor >= 4.0:                          # Diturunkan sedikit
            sinyal = "BUY"
            signal_strength = "C"
        # TIER 4: PANTAU (D) - Monitor, not ready yet
        elif confidence >= 30 and skor >= 2.0:                          # Diturunkan
            sinyal = "PANTAU"
            signal_strength = "D"
        # TIER 5: TUNGGU (E) - DEBUG MODE: BUKA SEMUA GEMBOK!
        elif skor >= -15.0:                                             # <-- Asal skor tidak hancur lebur banget, tampilkan!
            sinyal = "TUNGGU"
            signal_strength = "E"
        # DEFAULT: HINDARI
        else:
            sinyal = "HINDARI"
            signal_strength = "F"

        # ─── PREDIKSI KECERDASAN BUATAN (AI) ───
        # 1. Hitung memori masa lalu SEBELUM AI menebak
        rsi_1d = float(rsi.iloc[-2]) if len(rsi) > 1 else rsi_v
        try:
            ema12_kemarin = close.ewm(span=12, adjust=False).mean().iloc[-2]
            ema26_kemarin = close.ewm(span=26, adjust=False).mean().iloc[-2]
            macd_1d = float(ema12_kemarin - ema26_kemarin)
        except:
            macd_1d = macd_v
        
        # =====================================================================
        # 🤖 PROSES PREDIKSI AI (LIQUID NETWORK INFERENCE)
        # =====================================================================
        ai_win_prob = 0.0
        ai_verdict = "TIDAK DIUJI"

        if sinyal != "HINDARI":
            try:
                nilai_mm = mm_activity.get("confidence", 0) if isinstance(mm_activity, dict) else 0
                nilai_retail = retail_comparison.get("mm_vs_retail_ratio", 0) if isinstance(retail_comparison, dict) else 0

                # Phase-2: Extended 14-feature vector
                # Original 11 features kept in same order (no breaking change to model API).
                # 3 new features appended; ai_model.py FEATURE_NAMES extended accordingly.
                hari_ini = [
                    rsi_v, adx_v, vol_strength, bb_width, rrr,
                    nilai_mm, nilai_retail,
                    IHSG_CHANGE, USD_CHANGE, rsi_1d, macd_1d,
                    rsi_vol_interaction,   # Phase-2 NEW #12
                    rolling_vol_20,        # Phase-2 NEW #13
                    sector_corr,           # Phase-2 NEW #14
                ]

                ai_instance = get_ai_model(model_type="swing")
                
                # 3. Langsung masukkan 'hari_ini' ke dalam fungsi prediksi
                ai_win_prob = ai_instance.predict_win_probability(hari_ini)

                if ai_win_prob >= 60:
                    ai_verdict = "ULTRA BUY"
                elif ai_win_prob >= 50:
                    ai_verdict = "BUY"
                else:
                    ai_verdict = "WEAK"

                # Phase-4: replace print with logger
                logger.info("[AI] %s — Win Rate: %.1f%%  Verdict: %s", ticker, ai_win_prob, ai_verdict)

            except Exception as e:
                # Phase-1 & Phase-4: structured error log instead of raw print+traceback
                logger.error("[AI] Error memproses %s: %s", ticker, e, exc_info=True)

        # ─── TIER 3: Position Sizing (Kelly Criterion) ────────────────────────────
        assumed_account = 10000000
        position_sizing = position_size_calc(assumed_account, 1, price, stop_loss)

        # 🔥 Hitung Manajemen Risiko DI SINI (setelah AI selesai mikir)
        saran_lot = "0 Lot"
        if "BUY" in ai_verdict:
            saran_lot = hitung_kelly_sizing(ai_win_prob, float(close.iloc[-1]))

        return {
            "Ticker"        : ticker.replace(".JK", ""),
            "Sektor"        : PETA_SEKTOR.get(ticker, "-"),
            "Harga"         : int(price),
            "Skor"          : round(skor, 1),
            "Max"           : 15,
            "Sinyal"        : sinyal,
            "Strength"      : signal_strength,
            "Konfirmasi"    : " | ".join(konfirmasi[:6]) if konfirmasi else "—",
            "Confidence%"   : int(confidence),
            "Tech_Score"    : int(tech_score),        # 35% weight
            "Fund_Score"    : int(fund_score),        # 25% weight
            "RS_Score"      : int(rs_score),          # 20% weight
            "Sent_Score"    : int(sent_score),        # 20% weight
            "RSI"           : round(rsi_v, 1),
            "ADX"           : round(adx_v, 1),
            "Stoch"         : round(stoch_v, 1),
            "MACD"          : "UP" if macd_h > 0 else "DN",
            "Volume"        : int(vol_strength),
            "CCI"           : round(cci_v, 1),
            "VWAP_Signal"   : vwap_signal,
            "Pattern"       : pattern,
            "Divergence"    : divergence_signal,
            "Ichimoku"      : ichimoku_data["signal"],
            "BB_Width%"     : bb_width,
            "Support"       : int(support),
            "Resistance"    : int(resistance),
            "Stop_Loss"     : int(stop_loss),
            "Target_1"      : int(target_1),
            "Target_2"      : int(target_2),
            "Target_3"      : int(target_3),
            "Risk%"         : risk_pct,
            "Reward%"       : reward_pct,
            "RRR"           : rrr,
            "Position_Shares": position_sizing["shares"],
            "Position_IDR"  : position_sizing["position_size"],
            "Risk_Amount"   : position_sizing["risk_amount"],
            "Regime"        : regime,
            "Weekly_Trend"  : "Bull" if weekly_bullish else "Bear",
            "Monthly_Trend" : "Bull" if monthly_bullish else "Bear",
            "MM_Activity"   : mm_activity["activity"],
            "MM_Confidence" : int(mm_activity["confidence"]),
            "MM_Signals"    : ", ".join(mm_activity["accumulation_signals"][:2] + mm_activity["distribution_signals"][:2]) if mm_activity["accumulation_signals"] or mm_activity["distribution_signals"] else "NEUTRAL",
            "VPT_Trend"     : float(mm_activity["vpt_trend"]),
            "CMF_Signal"    : float(mm_activity["cmf_signal"]),
            "AD_Trend"      : float(mm_activity["ad_trend"]),
            "Volume_Spike"  : "YES" if mm_activity["volume_spike"] else "NO",
            "MM_Shares"     : mm_position["estimated_shares"],
            "MM_Value_IDR"  : mm_position["position_value_idr"],
            "MM_Float_Pct"  : round(mm_position["float_percentage"], 2),
            "MM_Float_Shares" : mm_position["float_shares"],
            "MM_Shares_Outstanding" : mm_position["shares_outstanding"],
            "MM_Float_Estimated" : bool(mm_position.get("float_estimated", False)),
            "MM_Intensity"  : round(mm_position["accumulation_intensity"], 2),
            "MM_Volume_Base": mm_position["volume_base"],
            "Retail_Shares"     : retail_comparison["retail_shares"],
            "Retail_Value_IDR"  : retail_comparison["retail_value_idr"],
            "Institutional_Shares": retail_comparison["institutional_shares"],
            "MM_vs_Retail_Ratio": retail_comparison["mm_vs_retail_ratio"],
            "MM_vs_Float_Ratio" : retail_comparison["mm_vs_float_ratio"],
            "Dominance"         : retail_comparison["dominance"],
            "Float_Shares" : int(fundamentals.get("float_shares", 0)),
            "Float_Source" : fundamentals.get("float_source", "reported"),
            "Shares_Outstanding" : int(fundamentals.get("shares_outstanding", 0)),
            "Market_Cap_IDR" : int(fundamentals.get("market_cap", 0)),
            "Sentiment_Score" : round(sentiment.get("sentiment_score", 0.0), 2),
            "Sentiment_Label" : sentiment.get("sentiment_label", "NEUTRAL"),
            "News_Count" : sentiment.get("news_count", 0),
            "AI_Win_Prob%"      : ai_win_prob,
            "AI_Verdict"        : ai_verdict,
            "IHSG_Change"  : round(IHSG_CHANGE, 2),
            "USD_Change"   : round(USD_CHANGE, 2),
            "RSI_1d"       : round(rsi_1d, 2),
            "MACD_1d"      : round(macd_1d, 3),
            "Foreign_Status"    : foreign_data["foreign_status"],
            "Net_Foreign_5d"    : foreign_data["net_foreign_5d"],
            "Saran_Lot"         : saran_lot,
            "Broksum_Status"    : data_broksum["status_bandar"],
            "Broksum_Net_Vol"     : data_broksum["akumulasi_bersih"],
            # ── Phase-2 NEW feature columns (for downstream ML training) ──
            "RSI_Vol_Interaction": round(rsi_vol_interaction, 2),
            "Rolling_Vol_20"     : round(rolling_vol_20, 6),
            "Sector_Corr"        : round(sector_corr, 4),
        }

    except Exception as e:
        # Phase-1 & Phase-4: structured error log; loop in caller will continue
        logger.error("[CRITICAL] Gagal memproses saham %s — %s", ticker, e, exc_info=True)
        return None
# ─── VIRTUAL HEDGE FUND MANAGER (PAPER TRADING) ─────────────────────────────
def update_virtual_portfolio(df: pd.DataFrame):
    db_name = "portofolio_virtual.db"
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

    # Buat tabel jika belum ada
    cursor.execute('''CREATE TABLE IF NOT EXISTS akun (saldo_cash REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS posisi (ticker TEXT, harga_beli REAL, sl REAL, tp REAL, shares INTEGER, tanggal TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS histori (ticker TEXT, pnl REAL, status TEXT, tanggal TEXT)''')

    # Set modal awal Rp 100.000.000 jika database baru dibuat
    cursor.execute("SELECT saldo_cash FROM akun")
    row = cursor.fetchone()
    if not row:
        saldo = 100000000.0
        cursor.execute("INSERT INTO akun (saldo_cash) VALUES (?)", (saldo,))
    else:
        saldo = row[0]

    print(f"\n{C.BOLD}{C.BLUE}{'-'*80}")
    print(f"  💼 VIRTUAL HEDGE FUND MANAGER (Modal: Rp 100.000.000)")
    print(f"{'-'*80}{C.RESET}")

    # 1. CEK POSISI TERBUKA (Jual Otomatis jika kena SL / TP)
    cursor.execute("SELECT ticker, harga_beli, sl, tp, shares FROM posisi")
    posisi_open = cursor.fetchall()
    total_value_saham = 0
    
    if not posisi_open:
        print("  📭 Portofolio saat ini KOSONG (Belum ada saham yang di-hold).")

    for pos in posisi_open:
        tkr, h_beli, sl, tp, shares = pos
        data_saham = df[df['Ticker'] == tkr]
        
        if not data_saham.empty:
            harga_skrg = float(data_saham.iloc[0]['Harga'])
            total_value_saham += (harga_skrg * shares)

            # Eksekusi Jual Otomatis
            if harga_skrg <= sl:
                pnl = (harga_skrg - h_beli) * shares
                saldo += (harga_skrg * shares)
                cursor.execute("DELETE FROM posisi WHERE ticker = ?", (tkr,))
                cursor.execute("INSERT INTO histori VALUES (?, ?, ?, ?)", (tkr, pnl, "HIT STOP LOSS", str(datetime.date.today())))
                print(f"  🔴 {tkr} CUTLOSS! Terjual di SL (Rp {harga_skrg:,}). PnL: Rp {pnl:,.0f}")
            elif harga_skrg >= tp:
                pnl = (harga_skrg - h_beli) * shares
                saldo += (harga_skrg * shares)
                cursor.execute("DELETE FROM posisi WHERE ticker = ?", (tkr,))
                cursor.execute("INSERT INTO histori VALUES (?, ?, ?, ?)", (tkr, pnl, "HIT TAKE PROFIT", str(datetime.date.today())))
                print(f"  🟢 {tkr} PROFIT! Terjual di TP (Rp {harga_skrg:,}). PnL: +Rp {pnl:,.0f}")
            else:
                unrealized = (harga_skrg - h_beli) * shares
                warna_un = C.GREEN if unrealized > 0 else C.RED
                print(f"  🛡️ HOLD {tkr:<6} | Floating: {warna_un}Rp {unrealized:,.0f}{C.RESET} (Beli: Rp {h_beli:,.0f} -> Skrg: Rp {harga_skrg:,.0f})")
        else:
            total_value_saham += (h_beli * shares)

    # 2. BELI SAHAM BARU (Hanya saham SUPER AMAN dari AI)
    # Kriteria Eksekusi: Harus ULTRA_BUY dan Prediksi AI di atas 70%
    kandidat_beli = df[(df['Sinyal'] == 'ULTRA_BUY') & (df['AI_Win_Prob%'] >= 70)]

    for _, row_saham in kandidat_beli.iterrows():
        tkr = row_saham['Ticker']
        cursor.execute("SELECT * FROM posisi WHERE ticker = ?", (tkr,))
        if cursor.fetchone():
            continue # Abaikan jika sudah punya saham ini di portofolio

        harga = float(row_saham['Harga'])
        max_alokasi = saldo * 0.20 # Max 20% modal (Rp 20 Juta) per saham agar tidak all-in
        shares_to_buy = min(int(row_saham['Position_Shares']), int(max_alokasi / harga))

        if shares_to_buy > 100 and saldo >= (shares_to_buy * harga):
            biaya = shares_to_buy * harga
            saldo -= biaya
            sl = float(row_saham['Stop_Loss'])
            tp = float(row_saham['Target_1'])

            cursor.execute("INSERT INTO posisi VALUES (?, ?, ?, ?, ?, ?)",
                           (tkr, harga, sl, tp, shares_to_buy, str(datetime.date.today())))
            print(f"  🛒 BOT MEMBELI {tkr}: {shares_to_buy:,} shares @ Rp {harga:,.0f} (Total: Rp {biaya:,.0f})")

    # Simpan kondisi terbaru ke Database
    cursor.execute("UPDATE akun SET saldo_cash = ?", (saldo,))
    conn.commit()
    conn.close()

    total_equity = saldo + total_value_saham
    roi = ((total_equity - 100000000) / 100000000) * 100
    warna_roi = C.GREEN if roi >= 0 else C.RED

    print(f"\n  💵 Cash Tersisa : Rp {saldo:,.0f}")
    print(f"  📈 Total Equity : Rp {total_equity:,.0f}")
    print(f"  📊 Total ROI    : {warna_roi}{roi:.2f}%{C.RESET}")
# ─── Screener Utama ──────────────────────────────────────────────────────────
def jalankan_screener(
    interactive: bool = True,
    tickers: list[str] | None = None,
    workers: int = DEFAULT_MAX_WORKERS,
    email: str | None = None,
    output_dir: str | None = None,
    skip_backtest: bool = False,
    skip_optimize: bool = False,
    skip_alerts: bool = False,
    verbose: bool = False
):
    tickers = list(tickers) if tickers else SEMUA_TICKER
    
    if verbose:
        print(f"  Using {workers} worker(s), {len(tickers)} ticker(s)")
        
    tanggal = datetime.date.today().strftime("%d %B %Y")
    
    # LAZY LOAD: Compute sector momentum only once when screener starts
    compute_sector_momentum()
    update_macro_globals()   # ← TAMBAHKAN INI

    print(f"\n{C.BOLD}{C.CYAN}{'='*80}")
    print(f"  IHSG SCREENER v5.0 MARKET MAKER & AI DETECTION  --  {tanggal}")
    print(f"  23 Indicators | MM Activity Analysis | Machine Learning Prediction")
    print(f"{'='*80}{C.RESET}")
    
    output_dir = os.path.abspath(output_dir) if output_dir else os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"  Memindai {len(tickers)} saham dari {len(WATCHLIST_SEKTOR)} sektor...\n")
    
    if verbose:
        print(f"  Output directory: {output_dir}\n")

    hasil_semua = []
    total_tickers = len(tickers)

    # ── Phase-3: Dynamic worker count (12-16) + batch processing (50/batch) ──
    # Workers bumped from DEFAULT_MAX_WORKERS (8) to up to 14 for better throughput.
    # Batch size 50 prevents excessive memory use on large ticker lists.
    effective_workers = max(workers, min(14, (os.cpu_count() or 4) * 2))
    BATCH_SIZE_SCAN = 50   # Phase-3: process 50 tickers at a time

    if effective_workers > 1 and total_tickers > 1:
        print(f"  Running analysis with {effective_workers} workers (batch={BATCH_SIZE_SCAN})...\n")

        # Phase-3: split tickers into batches
        ticker_batches = [
            tickers[i:i + BATCH_SIZE_SCAN]
            for i in range(0, len(tickers), BATCH_SIZE_SCAN)
        ]

        for batch_idx, batch in enumerate(ticker_batches, 1):
            # Phase-1: wrap each batch in try/except/finally
            try:
                with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                    future_to_ticker = {
                        executor.submit(analisis_saham, ticker): ticker for ticker in batch
                    }
                    for future in as_completed(future_to_ticker):
                        ticker_done = future_to_ticker[future]
                        nama = ticker_done.replace(".JK", "")
                        try:
                            hasil = future.result(timeout=60)
                        except Exception as err:
                            hasil = None
                            # Phase-1 & Phase-4: log and continue — do not crash the whole loop
                            logger.error("Error analyzing %s: %s", nama, err)
                        if hasil:
                            hasil_semua.append(hasil)
                        completed = len(hasil_semua)
                        print(
                            f"  [B{batch_idx}/{len(ticker_batches)}] "
                            f"[{completed:02d}/{total_tickers}] {nama:<8}",
                            end="\r",
                        )
            except Exception as batch_err:
                # Phase-1: log batch-level failures, continue to next batch
                logger.error("Batch %d/%d gagal: %s", batch_idx, len(ticker_batches), batch_err)
            finally:
                # Phase-3: release batch memory immediately
                gc.collect()
                logger.debug("Batch %d/%d selesai — gc.collect() dipanggil", batch_idx, len(ticker_batches))

        print("\n", end="")
    else:
        # Phase-1: try/except/finally around single-worker loop
        for i, ticker in enumerate(tickers, 1):
            nama = ticker.replace(".JK", "")
            print(f"  [{i:02d}/{total_tickers}] Analisis {nama:<8}", end="\r")
            try:
                hasil = analisis_saham(ticker)
                if hasil:
                    hasil_semua.append(hasil)
            except Exception as loop_err:
                # Phase-1: log error and continue loop — don't abort entire scan
                logger.error("Loop error pada %s: %s", nama, loop_err)
            finally:
                pass  # Phase-1: placeholder for any future connection cleanup

    print(" " * 80, end="\r")

    if not hasil_semua:
        print(f"{C.RED}Gagal mengambil data. Periksa pesan error MERAH di atas!{C.RESET}")
        return

    df = pd.DataFrame(hasil_semua).sort_values("Confidence%", ascending=False)

    ultra_buy = df[df["Sinyal"] == "ULTRA_BUY"]
    strong_buy = df[df["Sinyal"] == "STRONG_BUY"]
    buy_signals = df[df["Sinyal"] == "BUY"]

    total_bullish = len(ultra_buy) + len(strong_buy) + len(buy_signals)

    print(f"\n{C.BOLD}{C.GREEN}{'-'*80}")
    print(f"  >> SINYAL BELI PREMIUM ({total_bullish} saham)")
    print(f"{'-'*80}{C.RESET}")

    if total_bullish == 0:
        print(f"  {C.YELLOW}Belum ada setup tradable dengan confidence tinggi.{C.RESET}\n")
    else:
        for signal_type, color, df_filter in [
            ("ULTRA BUY (RRR > 1.5)", C.BG_GREEN + C.BOLD, ultra_buy),
            ("STRONG BUY (Multi-TF)", C.GREEN + C.BOLD, strong_buy),
            ("BUY", C.GREEN, buy_signals)
        ]:
            if not df_filter.empty:
                print(f"\n  {color}{signal_type}{C.RESET}")
                print(f"  {'-'*78}")
                
                for _, row in df_filter.iterrows():
                    print(
                        f"    {C.BOLD}{row['Ticker']:<8}{C.RESET} "
                        f"[{row['Strength']}] Rp{row['Harga']:>9,} | "
                        f"Skor: {C.CYAN}{row['Skor']:.1f}/15{C.RESET} | "
                        f"Conf: {C.YELLOW}{row['Confidence%']:>3}%{C.RESET}"
                    )
                    
                    # ─── CETAK HASIL PREDIKSI AI DI SINI ───
                    if row.get('AI_Win_Prob%', 0) > 0:
                        warna_ai = C.GREEN if row['AI_Win_Prob%'] >= 70 else (C.YELLOW if row['AI_Win_Prob%'] >= 50 else C.RED)
                        print(f"    🤖 AI Prediction: {warna_ai}{C.BOLD}{row['AI_Win_Prob%']}% Win Rate ({row['AI_Verdict']}){C.RESET}")
                    
                    konfirmasi_str = row['Konfirmasi'].replace("X2", "**").replace("X", "*")
                    print(f"    Setup: {C.CYAN}{konfirmasi_str}{C.RESET}")
                    
                    print(
                        f"    Entry: {C.MAGENTA}{int(row['Harga'])}{C.RESET} | "
                        f"SL: {C.RED}Rp {row['Stop_Loss']:,} (-{row['Risk%']}%){C.RESET} | "
                        f"TP: {C.GREEN}Rp {row['Target_1']:,} (+{row['Reward%']}%){C.RESET} | "
                        f"RRR: {C.MAGENTA}{row['RRR']:.2f}{C.RESET}"
                    )
                    
                    print(
                        f"    Pattern: {row['Pattern']} | Ichimoku: {row['Ichimoku']} | "
                        f"VWAP: {row['VWAP_Signal']} | Div: {row['Divergence']}"
                    )
                    
                    print(
                        f"    MM Activity: {C.BOLD}{row['MM_Activity']}{C.RESET} "
                        f"({row['MM_Confidence']}%) | Signals: {row['MM_Signals']} | "
                        f"Vol Spike: {row['Volume_Spike']}"
                    )
                    
                    print(
                        f"    MM Position: {C.MAGENTA}{row['MM_Shares']:,}{C.RESET} shares "
                        f"(Rp {row['MM_Value_IDR']:,.0f}) | Float: {row['MM_Float_Pct']}% | "
                        f"Intensity: {row['MM_Intensity']:+.2f}"
                    )
                    
                    print(
                        f"    Retail vs MM: {C.CYAN}{row['Retail_Shares']:,}{C.RESET} retail shares | "
                        f"Ratio: {C.YELLOW}{row['MM_vs_Retail_Ratio']:.1f}%{C.RESET} | "
                        f"Dominance: {C.BOLD}{row['Dominance']}{C.RESET}"
                    )
                    
                    print(
                        f"    Shares: {row['Position_Shares']} | Risk: Rp {row['Risk_Amount']:,} | "
                        f"Regime: {row['Regime']} | Vol: {row['Volume']}% | "
                        f"W:{row['Weekly_Trend']} M:{row['Monthly_Trend']}"
                        f" | Saran: {C.BG_YELLOW}{C.BOLD}{row.get('Saran_Lot', 'N/A')}{C.RESET}"
                    )
                    print()

    print(f"\n{C.BOLD}{C.WHITE}{'-'*80}")
    print(f"  RINGKASAN WATCHLIST (Top 20 by Confidence)")
    print(f"{'-'*80}{C.RESET}")

    header = (
        f"  {'Ticker':<8} {'Str':>3} {'Skor':>5} {'Conf%':>5} {'Sinyal':<15} "
        f"{'Harga':>9} {'Ptrn':>7} {'RRR':>5} {'MM_Act':>6} {'MM_Shares':>9} {'Dominance':>10}"
    )
    print(f"{C.BOLD}{header}{C.RESET}")
    print(f"  {'-'*105}")

    for _, row in df.head(20).iterrows():
        mm_short = row['MM_Activity'][:3] if len(row['MM_Activity']) > 3 else row['MM_Activity']
        mm_shares_short = f"{row['MM_Shares']//1000}K" if row['MM_Shares'] >= 1000 else str(row['MM_Shares'])
        dominance_short = row['Dominance'][:3] if len(row['Dominance']) > 3 else row['Dominance']
        
        print(
            f"  {row['Ticker']:<8} "
            f"{row['Strength']:>3} "
            f"{C.CYAN}{row['Skor']:>5.1f}{C.RESET} "
            f"{C.YELLOW}{row['Confidence%']:>5}%{C.RESET} "
            f"{warna_sinyal(row['Sinyal']):<15} "
            f"Rp{row['Harga']:>9,} "
            f"{row['Pattern']:>7} "
            f"{C.MAGENTA}{row['RRR']:>5.2f}{C.RESET} "
            f"{mm_short:>6} "
            f"{mm_shares_short:>9} "
            f"{dominance_short:>10}"
        )

    print(f"\n{C.BOLD}{C.CYAN}{'-'*80}")
    print(f"  ANALISIS SEKTOR & MARKET REGIME")
    print(f"{'-'*80}{C.RESET}")

    sector_stats = defaultdict(lambda: {"total": 0, "buy": 0, "strong_buy": 0, "ultra_buy": 0})
    regime_count = defaultdict(int)

    for _, row in df.iterrows():
        sektor = row["Sektor"]
        sector_stats[sektor]["total"] += 1
        regime_count[row["Regime"]] += 1

        if row["Sinyal"] == "ULTRA_BUY":
            sector_stats[sektor]["ultra_buy"] += 1
        elif row["Sinyal"] == "STRONG_BUY":
            sector_stats[sektor]["strong_buy"] += 1
        elif row["Sinyal"] == "BUY":
            sector_stats[sektor]["buy"] += 1

    print(f"\n  Market Regime:")
    for regime, count in sorted(regime_count.items(), key=lambda x: x[1], reverse=True):
        pct = round(count / len(df) * 100)
        print(f"    {regime:<18}: {count:>2} saham ({pct:>3}%)")

    print(f"\n  Sektor terbaik:")
    sector_sorted = sorted(
        sector_stats.items(),
        key=lambda x: x[1]["ultra_buy"] * 3 + x[1]["strong_buy"] * 2 + x[1]["buy"],
        reverse=True
    )
    
    for sektor, stats in sector_sorted:
        total_signal = stats["ultra_buy"] + stats["strong_buy"] + stats["buy"]
        pct = round(total_signal / stats["total"] * 100) if stats["total"] > 0 else 0
        print(
            f"    {sektor:<25}: {total_signal}/{stats['total']} bullish "
            f"({pct:>3}%) -- {stats['ultra_buy']}UB {stats['strong_buy']}SB {stats['buy']}B"
        )

    print(f"\n{C.BOLD}{C.MAGENTA}{'-'*80}")
    print(f"  MARKET MAKER ACTIVITY ANALYSIS")
    print(f"{'-'*80}{C.RESET}")

    accumulation_count = len(df[df["MM_Activity"] == "ACCUMULATION"])
    distribution_count = len(df[df["MM_Activity"] == "DISTRIBUTION"])
    neutral_count = len(df[df["MM_Activity"] == "NEUTRAL"])
    
    high_conf_accum = len(df[(df["MM_Activity"] == "ACCUMULATION") & (df["MM_Confidence"] >= 75)])
    high_conf_dist = len(df[(df["MM_Activity"] == "DISTRIBUTION") & (df["MM_Confidence"] >= 75)])

    print(f"  Accumulation  : {C.GREEN}{accumulation_count:>3}{C.RESET} saham ({C.BOLD}{high_conf_accum}{C.RESET} high confidence)")
    print(f"  Distribution  : {C.RED}{distribution_count:>3}{C.RESET} saham ({C.BOLD}{high_conf_dist}{C.RESET} high confidence)")
    print(f"  Neutral       : {C.GRAY}{neutral_count:>3}{C.RESET} saham")

    total_mm_value = df['MM_Value_IDR'].sum()
    avg_mm_shares = df['MM_Shares'].mean()
    max_mm_position = df['MM_Shares'].max()
    max_mm_ticker = df.loc[df['MM_Shares'].idxmax(), 'Ticker']
    
    print(f"\n  {C.CYAN}Market Maker Position Summary:{C.RESET}")
    print(f"  Total MM Value    : {C.BOLD}Rp {total_mm_value:,.0f}{C.RESET}")
    print(f"  Average MM Shares : {C.BOLD}{avg_mm_shares:,.0f}{C.RESET}")
    print(f"  Largest Position  : {C.BOLD}{max_mm_position:,.0f}{C.RESET} shares ({max_mm_ticker})")

    total_retail_value = df['Retail_Value_IDR'].sum()
    avg_mm_vs_retail_ratio = df['MM_vs_Retail_Ratio'].mean()
    
    dominance_counts = df['Dominance'].value_counts()
    mm_dominant = dominance_counts.get('MM_DOMINANT', 0) + dominance_counts.get('MM_STRONG', 0)
    balanced = dominance_counts.get('BALANCED', 0)
    retail_dominant = dominance_counts.get('RETAIL_DOMINANT', 0) + dominance_counts.get('MM_MODERATE', 0)
    
    print(f"\n  {C.YELLOW}Retail vs Market Maker Comparison:{C.RESET}")
    print(f"  Total Retail Value    : {C.BOLD}Rp {total_retail_value:,.0f}{C.RESET}")
    print(f"  Avg MM/Retail Ratio  : {C.BOLD}{avg_mm_vs_retail_ratio:.1f}%{C.RESET}")
    print(f"  MM Dominant          : {C.RED}{mm_dominant:>2}{C.RESET} stocks")
    print(f"  Balanced             : {C.GRAY}{balanced:>2}{C.RESET} stocks")
    print(f"  Retail Dominant      : {C.GREEN}{retail_dominant:>2}{C.RESET} stocks")

    top_mm_impact = df.sort_values("MM_vs_Float_Ratio", ascending=False).head(5)
    if not top_mm_impact.empty:
        print(f"\n  {C.MAGENTA}Top MM Impact Stocks (Highest MM % of Float):{C.RESET}")
        for _, row in top_mm_impact.iterrows():
            print(
                f"    {row['Ticker']:<8} - {row['MM_vs_Float_Ratio']:.2f}% of float | "
                f"MM: {row['MM_Shares']:,} | Retail: {row['Retail_Shares']:,} | "
                f"Dominance: {row['Dominance']}"
            )

    top_mm_retail = df.sort_values("MM_vs_Retail_Ratio", ascending=False).head(5)
    if not top_mm_retail.empty:
        print(f"\n  {C.CYAN}Top MM vs Retail Stocks:{C.RESET}")
        for _, row in top_mm_retail.iterrows():
            print(
                f"    {row['Ticker']:<8} - {row['MM_vs_Retail_Ratio']:.2f}% | "
                f"Retail: {row['Retail_Shares']:,} | MM: {row['MM_Shares']:,}"
            )

    accum_stocks = df[(df["MM_Activity"] == "ACCUMULATION") & (df["MM_Confidence"] >= 75)].head(5)
    if not accum_stocks.empty:
        print(f"\n  {C.GREEN}Top Accumulation Stocks (High Confidence):{C.RESET}")
        for _, row in accum_stocks.iterrows():
            shares_str = f"{row['MM_Shares']//1000}K" if row['MM_Shares'] >= 1000 else str(row['MM_Shares'])
            print(f"    {row['Ticker']:<8} - {shares_str:>6} shares - {row['MM_Signals']} (Conf: {row['MM_Confidence']}%)")

    if distribution_count > 0:
        print(f"\n  {C.RED}Top Distributing Stocks (High Confidence):{C.RESET}")
        dist_stocks = df[(df["MM_Activity"] == "DISTRIBUTION") & (df["MM_Confidence"] >= 75)].head(5)
        for _, row in dist_stocks.iterrows():
            shares_str = f"{row['MM_Shares']//1000}K" if row['MM_Shares'] >= 1000 else str(row['MM_Shares'])
            print(f"    {row['Ticker']:<8} - {shares_str:>6} shares - {row['MM_Signals']} (Conf: {row['MM_Confidence']}%)")

    strong_buy_count = len(df[df["Sinyal"] == "STRONG_BUY"])
    buy_count        = len(df[df["Sinyal"] == "BUY"])
    pantau_count     = len(df[df["Sinyal"] == "PANTAU"])
    hindari_count    = len(df[df["Sinyal"] == "HINDARI"])
    ultra_buy_count  = len(df[df["Sinyal"] == "ULTRA_BUY"])
    
    # 🟢 1. TAMBAHKAN PENGHITUNG "TUNGGU" DI SINI
    tunggu_count     = len(df[df["Sinyal"] == "TUNGGU"]) 
    
    total            = len(df)
    
    # (Pastikan rumus bullish_pct tetap sama)
    bullish_pct      = round(((ultra_buy_count + strong_buy_count + buy_count) / total) * 100)

    print(f"\n{C.BOLD}{C.CYAN}{'-'*80}")
    print(f"  KONDISI PASAR (dari {total} saham dipindai)")
    print(f"{'-'*80}{C.RESET}")
    print(f"  ULTRA Buy   : {C.BG_GREEN}{C.BOLD}{ultra_buy_count:>3}{C.RESET} saham")
    print(f"  STRONG Buy  : {C.GREEN}{strong_buy_count:>3}{C.RESET} saham")
    print(f"  Buy        : {C.GREEN}{buy_count:>3}{C.RESET} saham")
    print(f"  PANTAU     : {C.YELLOW}{pantau_count:>3}{C.RESET} saham")
    
    # 🟢 2. TAMBAHKAN TAMPILAN "TUNGGU" DI SINI
    print(f"  TUNGGU     : {C.GRAY}{tunggu_count:>3}{C.RESET} saham")
    
    print(f"  HINDARI    : {C.RED}{hindari_count:>3}{C.RESET} saham")
    print(f"\n  Sentimen Bullish: {C.BOLD}{bullish_pct}%{C.RESET} ", end="")

    if bullish_pct >= 60:
        print(f"{C.BG_GREEN}{C.BOLD}{C.WHITE} PASAR SANGAT BULLISH {C.RESET}")
    elif bullish_pct >= 40:
        print(f"{C.GREEN}{C.BOLD} PASAR BULLISH {C.RESET}")
    elif bullish_pct >= 20:
        print(f"{C.YELLOW}>> Pasar dalam kondisi MIXED{C.RESET}")
    else:
        print(f"{C.RED}>> Pasar dalam kondisi BEARISH / RISK-OFF{C.RESET}")

    filename = os.path.join(output_dir, EXPORT_FILENAME_TEMPLATE.format(date=datetime.date.today().strftime('%Y%m%d')))
    df_export = df.drop(columns=["Max"], errors='ignore')
    df_export.to_csv(filename, index=False)
    print(f"\n  Hasil disimpan ke: {C.CYAN}{filename}{C.RESET}")
    
    # ── Export ke Parquet Data Lake v1.0 Clean ──
    try:
        # Buat folder khusus data lake jika belum ada
        os.makedirs("data_lake", exist_ok=True)
        file_parquet = "data_lake/histori_ihsg.parquet"
        
        kolom_aman = [
            "Ticker", "Sektor", "Harga", "Skor", "Sinyal", "Strength", 
            "Confidence%", "RSI", "ADX", "Stoch", "MACD", "Volume", 
            "Regime", "MM_Activity", "MM_Confidence", "Dominance",
            "Stop_Loss", "Target_1", "RRR",
            "BB_Width%", "MM_vs_Retail_Ratio", "IHSG_Change", "USD_Change", 
            "RSI_1d", "MACD_1d", "RSI_Vol_Interaction", "Rolling_Vol_20", "Sector_Corr"
        ]
        
        existing_cols = [c for c in kolom_aman if c in df.columns]
        df_db = df[existing_cols].copy()
        df_db["Tanggal"] = datetime.date.today().isoformat()
        
        # Logika Penggabungan Data (Append ke Parquet)
        if os.path.exists(file_parquet):
            df_lama = pd.read_parquet(file_parquet)
            # Gabungkan data lama dan baru
            df_gabungan = pd.concat([df_lama, df_db], ignore_index=True)
            # Opsional: Hapus duplikat jika menjalankan screener 2x di hari yang sama
            df_gabungan = df_gabungan.drop_duplicates(subset=['Ticker', 'Tanggal'], keep='last')
        else:
            df_gabungan = df_db
            
        # Simpan dengan kompresi 'snappy' khas Parquet
        df_gabungan.to_parquet(file_parquet, engine='pyarrow', compression='snappy')
        
        print(f"  {C.GREEN}✓ Data berhasil ditabung ke Parquet Data Lake ({file_parquet}){C.RESET}")
        
    except Exception as e:
        print(f"  {C.RED}✗ Gagal menyimpan ke Parquet: {e}{C.RESET}")
        
    # ── Pemicu Virtual Hedge Fund Manager ──
    try:
        update_virtual_portfolio(df)
    except Exception as e:
        print(f"  {C.RED}✗ Gagal menjalankan Virtual Portfolio: {e}{C.RESET}")

    # ── Backtesting & Optimization (Fitur yang Dikembalikan) ──
    if skip_backtest:
        print(f"\n  {C.YELLOW}Backtesting skipped.{C.RESET}")
    else:
        print(f"\n{C.BOLD}{C.BLUE}{'-'*80}")
        print(f"  BACKTESTING RESULTS (Simulated)")
        print(f"{'-'*80}{C.RESET}")
        
        backtest_results = backtest_signals(df)
        if backtest_results and backtest_results.get("total_signals", 0) > 0:
            print(f"  Total Signals Tested: {backtest_results['total_signals']}")
            print(f"  Accumulation Win Rate: {backtest_results['acc_win_rate']:.1%}")
            print(f"  Distribution Win Rate: {backtest_results['dist_win_rate']:.1%}")
            print(f"  Avg Return (Accum): {backtest_results['avg_return_acc']:.2%}")
            print(f"  Avg Return (Dist): {backtest_results['avg_return_dist']:.2%}")
            print(f"  Sharpe (Accum): {backtest_results['sharpe_acc']:.2f}")
            print(f"  Max Drawdown (Accum): {backtest_results['max_drawdown_acc']:.2%}")
        else:
            print("  No high-confidence signals to backtest")

    if skip_optimize:
        print(f"\n  {C.YELLOW}Portfolio optimization skipped.\n{C.RESET}")
    else:
        print(f"\n{C.BOLD}{C.GREEN}{'-'*80}")
        print(f"  PORTFOLIO OPTIMIZATION")
        print(f"{'-'*80}{C.RESET}")
        
        opt_results = optimize_portfolio(df)
        if "error" not in opt_results:
            print(f"  Optimal Portfolio Weights:")
            for tkr_opt, weight in opt_results["optimal_weights"].items():
                print(f"    {tkr_opt}: {weight:.1%}")
            print(f"  Expected Return: {opt_results['expected_portfolio_return']:.2%}")
            print(f"  Portfolio Volatility: {opt_results['portfolio_volatility']:.2%}")
            print(f"  Sharpe Ratio: {opt_results['sharpe_ratio']:.2f}")
        else:
            print(f"  {opt_results['error']}")

    # ── Discord Alerts ──
    if skip_alerts:
        print(f"\n  {C.YELLOW}Alerts check skipped.\n{C.RESET}")
    else:
        check_and_alert(df, email=email)
        kirim_notifikasi_discord(df, DISCORD_WEBHOOK)

    print(f"\n{C.BOLD}{C.CYAN}{'='*80}{C.RESET}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IHSG Market Maker & AI Screener")
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS, help="Number of parallel workers")
    
    # 🔥 INI UPGRADE-NYA: Tambahkan "-t" sebagai jalan pintas (shortcut)
    parser.add_argument("-t", "--tickers", nargs="+", help="List of tickers to scan")
    
    parser.add_argument("--ticker-file", type=str, help="Read ticker symbols from a file")
    
    # 🔥 BONUS: Tambahkan "-s" untuk mode pemindaian 1 sektor spesifik
    parser.add_argument("-s", "--sector", type=str, help="Scan only a specific sector")
    
    parser.add_argument("--output-dir", type=str, default=".", help="Directory to save CSV")
    parser.add_argument("--email", type=str, help="Email address to send alerts")
    parser.add_argument("--skip-backtest", action="store_true", help="Skip the backtesting summary")
    parser.add_argument("--skip-optimize", action="store_true", help="Skip portfolio optimization")
    parser.add_argument("--skip-alerts", action="store_true", help="Skip alert checks")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    USE_CACHE = False

    tickers: list[str] = []
    if args.ticker_file:
        tickers.extend(load_tickers_from_file(args.ticker_file))
        
    if args.sector:
        sector_key = next((k for k in WATCHLIST_SEKTOR if k.lower() == args.sector.strip().lower()), None)
        if sector_key:
            tickers.extend(WATCHLIST_SEKTOR[sector_key])
            
    if args.tickers:
        tickers.extend(normalize_ticker_symbol(t) for t in args.tickers if normalize_ticker_symbol(t))
        
    if not tickers:
        tickers = SEMUA_TICKER
    else:
        tickers = list(dict.fromkeys(tickers))

    jalankan_screener(
        interactive=sys.stdin.isatty(),
        tickers=tickers,
        workers=max(1, args.workers),
        email=args.email,
        output_dir=args.output_dir,
        skip_backtest=args.skip_backtest,
        skip_optimize=args.skip_optimize,
        skip_alerts=args.skip_alerts,
        verbose=args.verbose,
    )