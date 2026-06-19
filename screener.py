"""
============================================================
  IHSG STOCK SCREENER v10.0 - The Profit Maximizer
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
# ─────────────────────────────────────────────────────────────────
# DEKONSTRUKSI v1.0 — screener.py has been refactored into
# separate modules under core/ and utils/. All indicator, scoring,
# scraper, file I/O, helper, and notification logic has been
# extracted. This file now only contains orchestration functions:
#   analisis_saham(), jalankan_screener(), parse_args(), __main__
# ─────────────────────────────────────────────────────────────────

import sys
import io

# Fix UTF-8 encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd

# ═══════════════════════════════════════════════════════════════════════
# IMPORTS FROM DEKONSTRUKSI MODULES
# ═══════════════════════════════════════════════════════════════════════

# core/indicators — all technical analysis functions
from core.indicators import (
    calculate_sma, calculate_ema, calculate_rsi, calculate_macd,
    calculate_adx, calculate_bollinger_bands, calculate_atr,
    calculate_obv, calculate_vwap,
    hma, detect_support_resistance, market_regime,
    volume_analysis, volume_price_trend, chaikin_money_flow,
    ease_of_movement, volume_oscillator, accumulation_distribution,
    detect_market_maker_activity, estimate_market_maker_position,
    estimate_retail_vs_mm_comparison, detect_divergence,
    calculate_ichimoku_cloud, pattern_recognition,
    detect_zscore_anomaly,
)

# core/scoring — scoring engine + portfolio + sector data
from core.scoring import (
    BOBOT_SKOR, WATCHLIST_SEKTOR, SEMUA_TICKER, PETA_SEKTOR,
    SEKTOR_MOMENTUM, AI_AKTIF,
    compute_confidence, get_calibrated_win_prob, get_signal,
    get_adaptive_weights, _normalize_score,
    hitung_kelly_sizing, _predict_ensemble,
    compute_sector_momentum,
    backtest_signals, build_covariance_matrix, optimize_portfolio,
    position_size_calc,
)

# core/scraper — data fetching + macro vars + scraping functions
from core.scraper import (
    fetch_price_data_sync, fetch_macro_data,
    get_macro_data, update_macro_globals,
    fetch_foreign_flow, fetch_berita_lokal,
    fetch_fundamental_metrics, fetch_news_sentiment,
    validasi_data_yfinance,
    IHSG_CHANGE, SP500_CHANGE, USD_CHANGE, BRENT_CHANGE,
    GOLD_CHANGE, COAL_CHANGE, USD_PRICE, ihsg_data,
    MACRO_PENALTY, IHSG_TREND,
)
import core.scraper as data_fetcher  # backward compat

# ── Make fetch_price_data the primary alias used throughout ──────────
fetch_price_data = fetch_price_data_sync

# core/file_handler — cache + file I/O
from core.file_handler import (
    get_cache_key, load_from_cache, save_to_cache,
    normalize_ticker_symbol, load_tickers_from_file,
    CACHE_DIR, EXPORT_FILENAME_TEMPLATE,
)

# utils/helpers — ANSI colors, safe converters, lazy imports
from utils.helpers import (
    C, warna_sinyal, _safe_float, safe_int, safe_float as safe_float_helper,
    _lazy_func,
)

# utils/notifications — alerts, Discord, virtual portfolio
from utils.notifications import (
    send_email_alert, check_and_alert,
    kirim_notifikasi_discord, update_virtual_portfolio,
)

# ── Remap safe_float from helpers (avoid confusion with local) ────────
safe_float = safe_float_helper

# Lazy stubs — modules may not exist yet
_analisis_broksum       = _lazy_func("broker_scraper", "analisis_broksum", lambda t: {"status_bandar":"NEUTRAL","akumulasi_bersih":0,"rasio_top3":0.0})
_detect_mean_reversion  = _lazy_func("mean_reversion", "detect_mean_reversion", lambda *a,**kw: {"signal":"NONE","confidence":0,"entry":0,"tp":0,"sl":0,"reason":[]})
_monte_carlo_size       = _lazy_func("monte_carlo", "suggest_size", lambda wp,rrr: "0 Lot (module missing)")
_journal_log_entry      = _lazy_func("trade_journal", "log_entry", lambda *a,**kw: None)
_journal_log_exit       = _lazy_func("trade_journal", "log_exit", lambda *a,**kw: None)
_get_sentiment          = _lazy_func("nlp_scraper", "get_sentiment", lambda t: (0.0, "NEUTRAL"))
_get_ai_model           = _lazy_func("ai_model", "get_ai_model", None)

# =======================================================
# SENSOR MARKET BREADTH v9.2 (Lazy Loading)
# =======================================================
global_total_discan = 0
global_saham_uptrend = 0

# ── Macro vars are imported from core.scraper ────────────────────────
# IHSG_CHANGE, SP500_CHANGE, USD_CHANGE, etc. now live in core/scraper.py
import threading
_counter_lock = threading.Lock()
# Load environment variables for API keys (never hardcoded)
import os
from dotenv import load_dotenv
load_dotenv()
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
import numpy as np
import datetime
import warnings
import os
import json
import argparse
import time
import requests
import traceback
import sqlite3
import logging
import gc  # Phase-3: batch memory management
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Phase-4: Unified logging setup with timestamp + persistent file ──────────
# FIX: Log every order, fill, and error event to persistent storage (SKILL.md ✓)
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"screener_{datetime.date.today():%Y%m%d}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
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
# BOBOT_SKOR, AI_AKTIF, etc. now imported from core/scoring

# ─── Caching Setup ───────────────────────────────────────────────────────────
# CACHE_DIR, USE_CACHE, EXPORT_FILENAME_TEMPLATE now imported from core/file_handler
DEFAULT_MAX_WORKERS = min(8, max(2, (os.cpu_count() or 4)))

# ─── Warna Terminal, safe converters, sector data, indicators, MM analysis ──
# All moved to:
#   - utils/helpers (class C, warna_sinyal, safe_int, safe_float)
#   - core/scoring (WATCHLIST_SEKTOR, SEKTOR_MOMENTUM, compute_sector_momentum, _download_batch)
#   - core/indicators (hma, detect_support_resistance, market_regime, ...)
#   - core/scraper (fetch_fundamental_metrics, fetch_news_sentiment, validasi_data_yfinance, ...)
#   - core/file_handler (get_cache_key, load_from_cache, save_to_cache, ...)
#   - utils/notifications (send_email_alert, check_and_alert, kirim_notifikasi_discord, update_virtual_portfolio)
# ─────────────────────────────────────────────────────────────────────────────

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
        open_price = _safe_float(open_series)
        ema21 = calculate_ema(close, 21)
        ema50 = calculate_ema(close, 50)
        hma20 = hma(close, 20)
        # 🔥 Update Sensor Market Breadth
        with _counter_lock:
            global_total_discan += 1
            if _safe_float(close) > _safe_float(ema50):
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
        # FIX 5.2: Jangan paksakan default PE/PBV — set flag missing
        _raw_pe  = fundamentals.get("trailing_pe", 0) or 0
        _raw_bv  = fundamentals.get("book_value",  0) or 0
        fundamentals_missing = False
        if _raw_pe == 0:
            logger.warning("[FUNDAMENTAL] %s: PE tidak tersedia — set flag missing", ticker)
            fundamentals["trailing_pe"] = 0
            fundamentals_missing = True
        if _raw_bv == 0:
            logger.warning("[FUNDAMENTAL] %s: Book Value tidak tersedia — set flag missing", ticker)
            fundamentals["book_value"] = 0
            fundamentals_missing = True

        # 🔥 Taruh Sensor Asing di sini, berkumpul dengan data eksternal lain
        foreign_data = fetch_foreign_flow(ticker)
        # Panggil fungsi dari file broker_scraper.py
        data_broksum = _analisis_broksum(ticker)
        # 🔥 Panggil Senjata Baru v9.0
        vol_zscore = detect_zscore_anomaly(volume)
        sentimen_lokal = fetch_berita_lokal(ticker)
        bulan_sekarang = datetime.date.today().month  # Untuk Time Encoding

        price = _safe_float(close)
        # Deklarasi awal 'vwap' agar terhindar dari UnboundLocalError
        vwap = close 
        try:
            vwap = VolumeWeightedAveragePrice(high=high, low=low, close=close, volume=volume).volume_weighted_average_price()
            vwap_v = _safe_float(vwap, price)
        except Exception:
            vwap_v = price

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
        
        ema21_val = _safe_float(ema21)
        ema50_val = _safe_float(ema50)
        hma_val = _safe_float(hma20)
        adx_v = _safe_float(adx_val)
        macd_v = _safe_float(macd_line)
        macd_s = _safe_float(macd_signal)
        macd_h = _safe_float(macd_hist)
        rsi_v = _safe_float(rsi)
        stoch_v = _safe_float(stoch_k)
        stoch_d_v = _safe_float(stoch_d)
        bb_mid_v = _safe_float(bb_mid)
        bb_up_v = _safe_float(bb_up)
        bb_low_v = _safe_float(bb_low)
        vol_v = _safe_float(volume)
        vol_sma_v = _safe_float(vol_sma20)
        atr_v = _safe_float(atr)
        obv_v = _safe_float(obv)
        obv_ma = _safe_float(obv.rolling(20).mean())
        
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
        cci_v = _safe_float(cci_series)
        
        vwap_signal = "ABOVE" if price > vwap_v else "BELOW"
        vwap_slope = _safe_float(vwap.iloc[-1] - vwap.iloc[-5]) if len(vwap) >= 5 else 0.0
        vwap_trend = vwap_slope > 0
        
        kc_high = bb_mid_v + (2.0 * atr_v)
        kc_low = bb_mid_v - (2.0 * atr_v)
        kc_signal = "NEAR_TOP" if price > (kc_high + kc_low) / 2 else "NEAR_BOTTOM"

        macd_hist_delta = _safe_float(macd_hist.iloc[-1] - macd_hist.iloc[-3]) if len(macd_hist) >= 3 else 0.0
        obv_trend = obv_v - obv_ma
        breakout_strength = price > resistance and vol_v > vol_sma_v * 1.3

        # ── Phase-2: NEW engineered features ─────────────────────────────────
        # 1. RSI × Volume interaction (momentum quality filter)
        #    Tinggi = RSI kuat + volume besar → sinyal lebih valid
        rsi_vol_interaction = float(rsi_v * vol_strength)

        # 2. Rolling 20-day realised volatility (annualised)
        #    Dipakai sebagai input AI dan risk-adjusted scoring
        rolling_vol_20 = _safe_float(close.pct_change().rolling(20).std())

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
                    # FIX 3.1: Gunakan pd.Series.corr() yang lebih efisien
                    corr_val = stock_ret.tail(min_len).corr(ihsg_ret.tail(min_len))
                    sector_corr = 0.0 if np.isnan(corr_val) or not np.isfinite(corr_val) else round(corr_val, 4)
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
                weekly_bullish = _safe_float(close_w) > _safe_float(ema20_w)

            data_m = fetch_price_data(ticker, period="2y", interval="1mo")
            if not data_m.empty and len(data_m) >= 12:
                close_m = data_m["Close"].squeeze()
                ema12_m = EMAIndicator(close=close_m, window=12).ema_indicator()
                monthly_bullish = _safe_float(close_m) > _safe_float(ema12_m)
        except Exception:
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
                ihsg_ret_20 = (_safe_float(ihsg_data) - float(ihsg_data.iloc[-20])) / float(ihsg_data.iloc[-20]) * 100
                
                if stock_ret_20 > ihsg_ret_20 + 5: 
                    skor += BOBOT_SKOR["RS_Outperform"] # Diubah dari RS_Strong
                    konfirmasi.append("RS_Strong*")
                
                if ihsg_ret_20 < 0 and stock_ret_20 > 0: 
                    skor += BOBOT_SKOR["Alpha_Leader"]
                    konfirmasi.append("Alpha_Leader***")
        except Exception: pass

        # --- OPTIMASI 5: DETEKSI VCP (VOLATILITY CONTRACTION) ---
        highest_20 = float(high.tail(20).max())
        jarak_pucuk = (highest_20 - price) / price * 100
        atr_20_avg = float(atr.tail(20).mean())
        
        if 0 <= jarak_pucuk <= 4 and atr_v < atr_20_avg and vol_v < vol_sma_v * 0.7:
            skor += BOBOT_SKOR["VCP_Pattern"]          # Diubah dari VCP_Setup
            konfirmasi.append("VCP_Setup**")

        # --- OPTIMASI 6: WYCKOFF ABSORPTION (EFFORT VS RESULT) ---
        spread = _safe_float(high - low)
        if vol_v > vol_sma_v * 1.5 and spread < atr_v * 0.8:
            if close.iloc[-1] >= (high.iloc[-1] + low.iloc[-1]) / 2: 
                skor += BOBOT_SKOR["Wyckoff_Absorb"]
                konfirmasi.append("Wyckoff_Absorb***")

        # --- OPTIMASI 7: NLP WEB SCRAPER SENTIMEN BERITA ---
        try:
            sent_score, sent_label = _get_sentiment(ticker)
            if sent_label == "BULLISH":
                skor += BOBOT_SKOR["News_BULLISH"]
                konfirmasi.append("News_BULLISH***")
            elif sent_label == "BEARISH":
                skor += BOBOT_SKOR["News_BEARISH"]
                konfirmasi.append("News_BEARISH---")
        except Exception:
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

        if 30 <= rsi_v <= 55: skor += 1.0   # Standardized RSI consistent dengan tech_score
        if stoch_v < 80 and stoch_v > stoch_d_v: skor += 1.0
        
        if price > bb_mid_v and price > support: skor += 1.0
        if vol_v > vol_sma_v * 1.2 and obv_v > obv_ma: skor += 1.0

        if breakout_strength:
            skor += 0.7
            konfirmasi.append("Breakout+")

        skor = min(skor, 15.0)

        # ── Manajemen Risiko v10.0 (TRAILING STOP + DYNAMIC SL) ────────────────
        risk_factor = 1.5 if regime == "HIGH_VOLATILITY" else 1.2
        base_sl = round(price - (risk_factor * atr_v))
        if weekly_bullish and monthly_bullish:
            base_sl = round(price - (risk_factor * atr_v * 1.3))
        stop_loss = max(base_sl, round(price * 0.92))
        target_1 = round(price + (2.0 * atr_v))
        target_2 = round(price + (3.5 * atr_v))
        target_3 = round(price + (5.0 * atr_v))

        risk_pct = round(((price - stop_loss) / price) * 100, 1)
        reward_pct = round(((target_1 - price) / price) * 100, 1)
        rrr = round(reward_pct / risk_pct, 2) if risk_pct != 0 else 0

        # ─── COMPONENT-BASED CONFIDENCE CALCULATION v11 (SINGLE SOURCE OF TRUTH) ───
        # Each component scored 0-100 independently
        # Weighting + normalization delegated to scoring_engine.compute_confidence()
        
        # TECHNICAL COMPONENT
        tech_score = 0
        if price > ema21_val > ema50_val and price > hma_val:
            tech_score += 20
        if 30 <= rsi_v <= 55:  # Standardized RSI range (fix inkonsistensi)
            tech_score += 15
        if macd_h > 0 and len(macd_hist) > 1 and macd_h > macd_hist.iloc[-2]:
            tech_score += 15
        if vol_v > vol_sma_v * 1.5:
            tech_score += 15
        if adx_v >= 25:  # Standardized ADX threshold
            tech_score += 20
        if pattern in ["BREAKOUT", "REVERSAL"]:
            tech_score += 15
        tech_score = min(100, tech_score)
        
        # FUNDAMENTAL COMPONENT
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
        fund_score = min(100, max(-30, fund_score))
        
        # RELATIVE STRENGTH COMPONENT
        rs_score = 0
        try:
            if len(close) >= 20 and len(ihsg_data) >= 20:
                stock_ret_20 = (price - float(close.iloc[-20])) / float(close.iloc[-20]) * 100
                ihsg_ret_20 = (_safe_float(ihsg_data) - float(ihsg_data.iloc[-20])) / float(ihsg_data.iloc[-20]) * 100
                if stock_ret_20 > ihsg_ret_20 + 5:
                    rs_score += 30
                if stock_ret_20 > ihsg_ret_20:
                    rs_score += 20
        except Exception:
            pass
        if momentum_sektor > 1.5:
            rs_score += 25
        rs_score = min(100, rs_score)
        
        # SENTIMENT COMPONENT
        sent_score = 0
        if sentiment.get("sentiment_score", 0) > 0.25 and sentiment.get("news_count", 0) >= 2:
            sent_score += 30
        elif sentiment.get("sentiment_score", 0) > 0.1 and sentiment.get("news_count", 0) >= 2:
            sent_score += 15
        elif sentiment.get("sentiment_score", 0) < -0.2 and sentiment.get("news_count", 0) >= 2:
            sent_score -= 30
        if foreign_data["foreign_status"] == "ACCUMULATION":
            sent_score += 20
        elif foreign_data["foreign_status"] == "DISTRIBUTION":
            sent_score -= 25
        if mm_activity["activity"] == "ACCUMULATION":
            sent_score += 15
        sent_score = min(100, max(-50, sent_score))
        
        # ── v11: SINGLE SOURCE OF TRUTH — delegasikan ke scoring_engine ───
        pct_above_ema50 = 50.0  # Default — akan diisi oleh caller jika ada data
        confidence, skor, c_thresh_buy = compute_confidence(
            tech_score=tech_score, fund_score=fund_score,
            rs_score=rs_score, sent_score=sent_score,
            adx_val=adx_v, ihsg_change=IHSG_CHANGE, ihsg_trend=IHSG_TREND,
            weekly_bullish=weekly_bullish, monthly_bullish=monthly_bullish,
            pct_above_ema50=pct_above_ema50
        )
        market_downgrade = 1 if IHSG_CHANGE < -1.0 else (0.5 if IHSG_CHANGE < -0.3 else 0)

        bb_width = round(((bb_up_v - bb_low_v) / bb_mid_v) * 100, 1)

        # ── ARB/ARA CIRCUIT BREAKER (IHSG ±35% limit) ─────────────────
        # FIX 1.1: Gunakan pct_change() yang handle NaN dan gap secara otomatis
        daily_change_pct = round(float(close.pct_change().iloc[-1] * 100), 1) if len(close) >= 2 else 0
        abs_change = abs(daily_change_pct)
        is_arb = abs_change >= 20 and daily_change_pct < 0
        is_ara = abs_change >= 20 and daily_change_pct > 0
        near_limit = abs_change >= 15

        # ── Penentuan Sinyal v10 (BALANCED SCORING + MACRO) ──────────────────────
        sinyal = "HINDARI"
        signal_strength = "F"
        
        # Thresholds yang disesuaikan dengan sistem confidence baru
        confidence_threshold_strong = 75 if IHSG_TREND == "UP" else 80
        confidence_threshold_buy = 55 if IHSG_TREND == "UP" else 65
        
        # FIX 2.3: Safety filter untuk fundamental buruk
        has_critical_risk = (
            per_val < 0 or                                  # EPS negatif
            pbv_val > 20 or                                 # PBV terlalu tinggi
            fundamentals.get("bankruptcy_risk", 0) > 0.3
        )
        
        if has_critical_risk:
            sinyal = "HINDARI"
            signal_strength = "RISK"
        # ARB/ARA: never recommend these
        elif is_arb:
            sinyal = "HINDARI"
            signal_strength = "ARB"
        elif is_ara:
            sinyal = "HINDARI"
            signal_strength = "ARA"
        elif near_limit and daily_change_pct < 0:
            # Near ARB — cap at PANTAU (never BUY)
            confidence = min(confidence, 40)
            skor = min(skor, 3.0)
            sinyal = "TUNGGU"
            signal_strength = "NEAR_ARB"
        elif near_limit and daily_change_pct > 0:
            # Near ARA — cap at PANTAU
            confidence = min(confidence, 40)
            skor = min(skor, 3.0)
            sinyal = "TUNGGU"
            signal_strength = "NEAR_ARA"
        # TIER 1: ULTRA_BUY (A+ / A) - Confluence of everything
        elif confidence >= 85 and skor >= 10 and rrr >= 1.8 and weekly_bullish and IHSG_TREND == "UP":
            sinyal = "ULTRA_BUY"
            signal_strength = "A+"
        elif confidence >= 80 and skor >= 9.5 and rrr >= 1.6 and weekly_bullish:
            sinyal = "ULTRA_BUY"
            signal_strength = "A"
        # TIER 2: STRONG_BUY (B+ / B) - High confidence + good trend + ADX filter
        elif confidence >= confidence_threshold_strong and skor >= 8.0 and adx_v >= 25:
            sinyal = "STRONG_BUY"
            signal_strength = "B+"
        elif confidence >= 70 and skor >= 7.0 and adx_v >= 25:
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
        except Exception:
            macd_1d = macd_v
        
        # =====================================================================
        # 🤖 PROSES PREDIKSI AI (ENSEMBLE then FALLBACK)
        # FIX: Dual AI coordination — ensemble_model.pkl > ai_model.py fallback
        # If ensemble_model.pkl exists, it is the source of truth (latih_ai.py v5).
        # Else, fall back to ai_model.py HistGradientBoosting (v4).
        # Both share different feature spaces; coordination via _predict_ensemble()
        # =====================================================================
        ai_win_prob = 0.0
        ai_verdict = "TIDAK DIUJI"

        if sinyal != "HINDARI":
            try:
                nilai_mm = mm_activity.get("confidence", 0) if isinstance(mm_activity, dict) else 0
                nilai_retail = retail_comparison.get("mm_vs_retail_ratio", 0) if isinstance(retail_comparison, dict) else 0

                # v5.0: Ensemble model (XGBoost+RF+HGB) with SMOTE training
                # 14 features matching latih_ai.py features_columns
                stoch_val = float(stoch_v)
                
                fitur_ensemble = [
                    skor, confidence, rsi_v, adx_v, stoch_val, cci_v,
                    bb_width, rrr,
                    nilai_mm, nilai_retail,
                    IHSG_CHANGE, USD_CHANGE, rsi_1d, macd_1d,
                ]

                # Try ensemble first, fall back to ai_model
                ai_win_prob = _predict_ensemble(fitur_ensemble)
                if ai_win_prob < 0:
                    # Ensemble not available — use fallback
                    logger.warning("[AI] %s -- Ensemble model MISSING. Using fallback heuristic.", ticker)
                    ai_instance = _get_ai_model(model_type="swing")
                    # Build 10-feature vector for ai_model
                    hari_ini = [
                        rsi_v, adx_v, vol_strength, rrr,
                        nilai_mm, nilai_retail,
                        IHSG_CHANGE, USD_CHANGE, macd_1d,
                        rolling_vol_20,
                    ]
                    ai_win_prob = ai_instance.predict_win_probability(hari_ini)

                if ai_win_prob >= 60:
                    ai_verdict = "ULTRA BUY"
                elif ai_win_prob >= 50:
                    ai_verdict = "BUY"
                else:
                    ai_verdict = "WEAK"

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
            if rrr >= 1.0 and ai_win_prob > 0:
                saran_lot = _monte_carlo_size(ai_win_prob, rrr)
            else:
                saran_lot = "0 Lot (RRR < 1.0 atau AI unavailable)"
        # v10.0: MEAN REVERSION DETECTION
        mr_result = {"signal": "NONE", "confidence": 0, "entry": 0, "tp": 0, "sl": 0, "reason": []}
        try:
            mr_result = _detect_mean_reversion(close, high, low, vol_sma20, rsi, bb_low, bb_up, bb_mid, atr, vol_sma20)
        except Exception:
            pass

        # v10.0: TRADE JOURNAL
        if sinyal in ("ULTRA_BUY", "STRONG_BUY", "BUY"):
            try:
                _journal_log_entry(ticker.replace(".JK",""), PETA_SEKTOR.get(ticker,"-"),
                                  int(price), position_sizing["shares"], sinyal, regime, "screener")
            except Exception:
                pass

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
            "MR_Signal"     : mr_result.get("signal", "NONE"),
            "MR_Confidence" : mr_result.get("confidence", 0),
            "MR_Entry"      : int(mr_result.get("entry", 0)),
            "MR_TP"         : int(mr_result.get("tp", 0)),
            "MR_SL"         : int(mr_result.get("sl", 0)),
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
) -> None:
    tickers = list(tickers) if tickers else SEMUA_TICKER
    
    if verbose:
        print(f"  Using {workers} worker(s), {len(tickers)} ticker(s)")
        
    tanggal = datetime.date.today().strftime("%d %B %Y")
    
    # LAZY LOAD: Compute sector momentum only once when screener starts
    compute_sector_momentum()
    update_macro_globals()   # ← TAMBAHKAN INI

    print(f"\n{C.BOLD}{C.CYAN}{'='*80}")
    # FIX: consistent version across all prints (v10.0)
    print(f"  IHSG SCREENER v10.0 MARKET MAKER & AI DETECTION  --  {tanggal}")
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
        print(f"  BACKTESTING RESULTS (Event-Driven)")
        print(f"{'-'*80}{C.RESET}")
        
        backtest_results = backtest_signals(df)
        if backtest_results and backtest_results.get("total_signals", 0) > 0:
            method = backtest_results.get("method", "unknown")
            print(f"  Total Signals: {backtest_results['total_signals']}")
            print(f"  Win Rate: {backtest_results['win_rate']:.1%}")
            print(f"  Sharpe Ratio: {backtest_results['sharpe_ratio']:.2f}")
            print(f"  Method: {method}")
            if method == "fallback (conservative estimate)":
                print(f"  {C.YELLOW}⚠ Run 'python backtest.py' for full historical backtest.{C.RESET}")
        else:
            print("  No tradable signals to backtest")

        if not skip_backtest:
            try:
                print(f"\n{C.CYAN}Walk-Forward Optimization:{C.RESET}")
                
                # ── Load historical screener CSVs for multi-date data ──────────
                import glob as _glob
                csv_dir = "Data Screener"
                history_frames = []
                # Collect from both root and Data Screener/ subfolder
                for _csv_path in sorted(_glob.glob("screener_ihsg_*.csv") +
                                        _glob.glob(os.path.join(csv_dir, "screener_ihsg_*.csv"))):
                    try:
                        _fname = os.path.basename(_csv_path)
                        # Parse date from filename: screener_ihsg_YYYYMMDD.csv
                        _date_str = _fname.replace("screener_ihsg_", "").replace(".csv", "")
                        if len(_date_str) == 8 and _date_str.isdigit():
                            _dt = f"{_date_str[:4]}-{_date_str[4:6]}-{_date_str[6:8]}"
                            _hdf = pd.read_csv(_csv_path)
                            if not _hdf.empty:
                                _hdf["Tanggal"] = _dt
                                history_frames.append(_hdf)
                        else:
                            continue
                    except Exception:
                        continue
                
                # Merge today's signals with historical, sorted by date
                today_copy = df.copy()
                today_copy["Tanggal"] = datetime.date.today().isoformat()
                history_frames.append(today_copy)
                all_signals = pd.concat(history_frames, ignore_index=True)
                
                # Build signals_by_date dict from merged data
                signals_by_date = {}
                for d, grp in all_signals.groupby("Tanggal"):
                    if d and len(grp) > 0:
                        signals_by_date[d] = grp.copy()
                
                n_days = len(signals_by_date)
                print(f"  Loaded {n_days} days of signal data ({len(history_frames)} files)")
                
                if n_days >= 6:
                    import sys as _sys, os as _os
                    _arch = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "archive")
                    if _arch not in _sys.path:
                        _sys.path.insert(0, _arch)
                    from backtest import walk_forward_optimize
                    wf_result = walk_forward_optimize(signals_by_date)
                    if "error" in wf_result:
                        print(f"  {C.YELLOW}Walk-forward skipped: {wf_result['error']}{C.RESET}")
                    else:
                        print(f"  Best SL mult: {wf_result['best_sl_mult']} | Best TP mult: {wf_result['best_tp_mult']}")
                        print(f"  Windows: {wf_result['n_windows']} | Positive: {wf_result['positive_windows']}")
                else:
                    print(f"  {C.YELLOW}Walk-forward skipped: Need ≥6 days, have {n_days}{C.RESET}")
            except Exception as wf_err:
                logger.debug("Walk-forward skipped: %s", wf_err)

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
        # FIX: only send Discord if webhook is configured
        check_and_alert(df, email=email)
        if DISCORD_WEBHOOK and DISCORD_WEBHOOK.startswith("http"):
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
    parser.add_argument("--cache", dest="cache", action="store_true", default=False,
                        help="Enable on-disk price cache (faster reruns, may serve stale data)")
    parser.add_argument("--no-cache", dest="cache", action="store_false",
                        help="Disable on-disk price cache (always fetch fresh)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    # Caching is controlled via --cache / --no-cache (default OFF for fresh data).
    # Must mutate the module attribute that core.scraper actually reads, not a local name.
    import core.file_handler as _fh
    _fh.USE_CACHE = bool(getattr(args, "cache", False))

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
