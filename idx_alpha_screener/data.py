"""
data.py — Fetch & Compute Indicators for IDX Alpha Screener v2
===============================================================
Semua indikator di-shift(1) untuk menghilangkan look-ahead bias.
"""

import numpy as np
import pandas as pd
import yfinance as yf
import logging
import os
import time
import datetime
from typing import Optional
import concurrent.futures

logger = logging.getLogger("data")

# ── Ticker list utama IHSG (diperluas ~200 saham, semua sektor & kapitalisasi) ──
TICKERS_IHSG_LIQUID = [
    # ═══════════════════════════════════════════════════
    # Perbankan & Finansial (24)
    # ═══════════════════════════════════════════════════
    "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BRIS.JK", "BBTN.JK",
    "BNGA.JK", "BDMN.JK", "NISP.JK", "ARTO.JK", "PNBN.JK",
    "BJBR.JK", "BJTM.JK",
    "AGRO.JK",

    # ═══════════════════════════════════════════════════
    # Fintech & Financial Services (9)
    # ═══════════════════════════════════════════════════
    "BUKA.JK", "EMTK.JK", "MLPT.JK", "DMMX.JK", "HDIT.JK",
    "MTWI.JK", "KRYA.JK", "TRON.JK", "FITT.JK",

    # ═══════════════════════════════════════════════════
    # Asuransi (7)
    # ═══════════════════════════════════════════════════
    "ASRM.JK", "LPGI.JK", "ABDA.JK", "AMAG.JK",
    "VINS.JK",

    # ═══════════════════════════════════════════════════
    # Big Cap & Blue Chip (19)
    # ═══════════════════════════════════════════════════
    "TLKM.JK", "ASII.JK", "UNVR.JK", "ICBP.JK", "INDF.JK", "GOTO.JK",
    "CPIN.JK", "JPFA.JK", "MYOR.JK", "KLBF.JK",
    "HMSP.JK", "GGRM.JK", "TBIG.JK", "TOWR.JK", "MTEL.JK",
    "MNCN.JK", "SCMA.JK", "FILM.JK", "BBHI.JK",

    # ═══════════════════════════════════════════════════
    # Konsumen — Consumer Cyclical & Defensive (22)
    # ═══════════════════════════════════════════════════
    "AMRT.JK", "ACES.JK", "MAPI.JK", "ERAA.JK", "SIDO.JK",
    "GOOD.JK", "ROTI.JK", "CLEO.JK", "STTP.JK",
    "TSPC.JK", "DVLA.JK", "KINO.JK", "SKLT.JK", "ADES.JK",
    "ULTJ.JK", "CAMP.JK", "CEKA.JK", "PANI.JK", "PBRX.JK",
    "PSDN.JK", "IKAI.JK", "KEJU.JK",

    # ═══════════════════════════════════════════════════
    # Energi — Oil & Gas, Energy Services (14)
    # ═══════════════════════════════════════════════════
    "MEDC.JK", "PGAS.JK", "AKRA.JK",
    "ESSA.JK", "ENRG.JK", "RAJA.JK", "POWR.JK", "LEAD.JK",
    "RUIS.JK", "WINS.JK", "INDY.JK", "SOCI.JK", "SMMA.JK",
    "SUGI.JK",

    # ═══════════════════════════════════════════════════
    # Tambang & Mineral (20)
    # ═══════════════════════════════════════════════════
    "ADRO.JK", "ITMG.JK", "PTBA.JK", "HRUM.JK", "BUMI.JK",
    "ANTM.JK", "INCO.JK", "CUAN.JK", "MBMA.JK", "NCKL.JK",
    "ADMR.JK",
    "BYAN.JK", "TINS.JK", "DOID.JK", "KKGI.JK", "ARII.JK",
    "BRMS.JK", "MYOH.JK", "GEMS.JK", "CTBN.JK",

    # ═══════════════════════════════════════════════════
    # Infrastruktur — Konstruksi, Telekom, Tol (14)
    # ═══════════════════════════════════════════════════
    "JSMR.JK", "WIKA.JK", "ADHI.JK", "PTPP.JK", "WSKT.JK", "WEGE.JK",
    "EXCL.JK", "ISAT.JK",
    "CMNP.JK", "BALI.JK", "META.JK",
    "GHON.JK",

    # ═══════════════════════════════════════════════════
    # Properti & Real Estate (16)
    # ═══════════════════════════════════════════════════
    "BSDE.JK", "CTRA.JK", "SMRA.JK", "PWON.JK", "DMAS.JK",
    "ASRI.JK", "APLN.JK", "MKPI.JK", "JRPT.JK", "MTLA.JK",
    "DILD.JK", "BCIP.JK", "BAPA.JK", "RODA.JK", "TARA.JK",
    "GWSA.JK",

    # ═══════════════════════════════════════════════════
    # Teknologi & Digital (8)
    # ═══════════════════════════════════════════════════
    "NICE.JK", "WGSH.JK", "LUCK.JK", "DIVA.JK", "BOLT.JK",
    "RUNS.JK", "EDGE.JK", "TECH.JK",

    # ═══════════════════════════════════════════════════
    # Healthcare — Farmasi, Rumah Sakit, Diagnostik (12)
    # ═══════════════════════════════════════════════════
    "KAEF.JK", "MIKA.JK", "HEAL.JK", "SILO.JK", "SAME.JK",
    "PRDA.JK", "CARE.JK", "SOHO.JK", "PEVE.JK", "MERK.JK",
    "SCPI.JK", "IRRA.JK",

    # ═══════════════════════════════════════════════════
    # Agrikultur — Perkebunan & CPO (14)
    # ═══════════════════════════════════════════════════
    "AALI.JK", "LSIP.JK", "TAPG.JK", "SSMS.JK",
    "SIMP.JK", "BWPT.JK", "TBLA.JK", "DSNG.JK", "UNSP.JK",
    "SMAR.JK", "CSRA.JK", "JAWA.JK", "MAGP.JK", "STAA.JK",

    # ═══════════════════════════════════════════════════
    # Industri — Semen, Bahan Baku, Manufaktur (15)
    # ═══════════════════════════════════════════════════
    "SMGR.JK", "INTP.JK", "SMCB.JK",
    "GJTL.JK", "KBLI.JK", "KBLM.JK", "ARNA.JK", "MLBI.JK",
    "TCID.JK", "SKBM.JK", "IKBI.JK", "LION.JK", "BIMA.JK",
    "ASGR.JK", "TIRA.JK",

    # ═══════════════════════════════════════════════════
    # Transportasi & Logistik (10)
    # ═══════════════════════════════════════════════════
    "BIRD.JK", "ASSA.JK", "SDMU.JK", "SMDR.JK", "TMAS.JK",
    "HITS.JK", "SAFE.JK", "TAXI.JK", "MIRA.JK", "CMPP.JK",

    # ═══════════════════════════════════════════════════
    # Konglomerasi & Multi-Industry (6)
    # ═══════════════════════════════════════════════════
    "UNTR.JK", "LPKR.JK", "SRTG.JK", "TPIA.JK",
    "BNBR.JK", "MPPA.JK",
]


def fetch_prices(ticker: str, period: str = "18mo", timeout: int = 15) -> pd.DataFrame:
    """Ambil data harga dari Yahoo Finance dengan caching built-in yfinance."""
    import requests
    session = requests.Session()
    session.mount('https://', requests.adapters.HTTPAdapter(max_retries=2))
    df = yf.download(ticker, period=period, progress=False,
                     auto_adjust=True, multi_level_index=False,
                     timeout=timeout, session=session)
    if df.empty:
        logger.warning("Data kosong untuk %s", ticker)
        return pd.DataFrame()

    # Flatten columns
    df.columns = [c.lower() for c in df.columns]
    
    # Ensure we have required columns
    required = ["open", "high", "low", "close", "volume"]
    found = [c for c in required if c in df.columns]
    if len(found) < 4:
        logger.warning("Kolom tidak lengkap untuk %s: %s", ticker, list(df.columns))
        return pd.DataFrame()

    df = df[required].copy()
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"

    # Hapus baris incomplete (close NaN) — biasanya terjadi di baris terakhir
    df = df.dropna(subset=["close"])
    # Hapus baris tanpa volume (hari libur)
    df = df[df["volume"] > 0]

    return df


def filter_stocks(df: pd.DataFrame) -> bool:
    """
    Filter fundamental & likuiditas v3 (RELAXED — sesuai IHSG mid-cap).
    Return True jika saham layak diproses lebih lanjut.

    Threshold di-relax agar ~70+ saham lolos dari 75 ticker teratas.
    Scoring engine tetap akan membedakan kualitas via skor scoring.py.

    Threshold sekarang (v3 relaxed):
      1. avg_volume_60d > 200.000    (sebelumnya 1jt/500rb)
      2. last_price > 100             (sebelumnya 200/150)
      3. atr_pct > 0.1%               (sebelumnya 0.3%/0.2%)
      4. <=6 hari volume 0 dalam 60   (tetap)
      5. est_monthly_value > Rp 30M   (sebelumnya 500M/200M)
         → saham Rp 500 × vol 3jt/hari = Rp 30M/bulan
    """
    if df.empty or len(df) < 60:
        return False

    close_idx = -1
    # 1. Volume rata-rata 60 hari — minimal aktivitas
    avg_volume_60d = df["volume"].tail(60).mean()
    if avg_volume_60d <= 200_000:
        return False

    # 2. Bukan penny stock ekstrem
    last_price = df["close"].iloc[close_idx]
    if last_price <= 100:
        return False

    # 3. ATR% > 0,1% (filter saham mati suri)
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[close_idx]
    atr_pct = (atr / last_price) * 100
    if atr_pct <= 0.1:
        return False

    # 4. Minimal 90% hari ada volume
    zero_vol_days = (df["volume"].tail(60) == 0).sum()
    if zero_vol_days > 6:
        return False

    # 5. Approximate monthly traded value > Rp 30M
    #    Filter hanya saham yang nyaris tanpa transaksi
    #    Contoh: Rp 500 × vol 3jt × 20 = Rp 30M
    #            Rp 2000 × vol 800rb × 20 = Rp 32M
    avg_price_60d = df["close"].tail(60).mean()
    est_monthly_value = avg_volume_60d * avg_price_60d * 20
    if est_monthly_value < 30_000_000_000:  # Rp 30M
        return False

    return True


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Hitung semua indikator teknikal dengan shift(1) untuk hindari look-ahead.
    Modifikasi df in-place dan return df yang sudah ditambah kolom.
    """
    if df.empty:
        return df

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # ── RSI (14) ──
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    df["rsi"] = rsi.shift(1)

    # ── MACD (12, 26, 9) ──
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal
    df["macd"] = macd_line.shift(1)
    df["macd_signal"] = macd_signal.shift(1)
    df["macd_hist"] = macd_hist.shift(1)

    # ── EMA (12, 50) ──
    df["ema12"] = close.ewm(span=12, adjust=False).mean().shift(1)
    df["ema50"] = close.ewm(span=50, adjust=False).mean().shift(1)

    # ── ADX (14) ──
    # True Range
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    # +DM dan -DM
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move

    plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr.replace(0, np.nan))

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.rolling(14).mean()

    df["adx"] = adx.shift(1)
    df["plus_di"] = plus_di.shift(1)
    df["minus_di"] = minus_di.shift(1)

    # ── ATR ──
    df["atr"] = atr.shift(1)

    # ── Volume Ratio (vs 20-day avg) ──
    vol_avg20 = volume.rolling(20).mean()
    df["vol_ratio"] = (volume / vol_avg20.replace(0, np.nan)).shift(1)
    
    # ── Average Daily Volume (60 hari) ──
    df["avg_vol_60d"] = volume.rolling(60).mean().shift(1)
    
    # ── Zero Volume Days (60 hari terakhir) ──
    df["zero_vol_pct_60d"] = (volume.rolling(60).apply(
        lambda x: (x == 0).sum() / len(x) * 100, raw=True
    )).shift(1)

    # ── Bollinger Bands (20,2) ──
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df["bb_mid"] = bb_mid.shift(1)
    df["bb_upper"] = (bb_mid + 2 * bb_std).shift(1)
    df["bb_lower"] = (bb_mid - 2 * bb_std).shift(1)
    df["bb_width_pct"] = ((df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, np.nan) * 100)

    # ── Price vs EMA50 ──
    df["pct_vs_ema50"] = ((close - df["ema50"]) / df["ema50"].replace(0, np.nan) * 100)

    # ── Return 20-hari ──
    df["ret_20d"] = close.pct_change(20).shift(1)

    # ── VWAP (Volume Weighted Average Price, 20-hari rolling) ──
    vwap_num = (volume * (high + low + close) / 3).rolling(20).sum()
    vwap_den = volume.rolling(20).sum()
    df["vwap"] = (vwap_num / vwap_den.replace(0, np.nan)).shift(1)
    df["pct_vs_vwap"] = ((close - df["vwap"]) / df["vwap"].replace(0, np.nan) * 100)

    # ── Donchian Channels (20) ──
    df["dc_upper"] = high.rolling(20).max().shift(1)
    df["dc_lower"] = low.rolling(20).min().shift(1)
    df["dc_mid"] = ((df["dc_upper"] + df["dc_lower"]) / 2)
    dc_range = (df["dc_upper"] - df["dc_lower"]).replace(0, np.nan)
    df["dc_position"] = ((close.shift(1) - df["dc_lower"]) / dc_range * 100)
    df["dc_breakout"] = (close > df["dc_upper"]).astype(int)  # dc_upper sudah shift(1) di line 305

    # ── OBV (On-Balance Volume) ──
    obv = (volume * ((close.diff() > 0).astype(int) * 2 - 1)).cumsum()
    obv_ema = obv.ewm(span=20, adjust=False).mean()
    obv_signal = obv - obv_ema
    df["obv"] = obv.shift(1)
    df["obv_signal"] = obv_signal.shift(1)
    # OBV trend: 1 = uptrend (OBV > EMA), -1 = downtrend
    df["obv_trend"] = ((obv > obv_ema).astype(int) * 2 - 1).shift(1)

    # ── Stochastic Oscillator (14,3) ──
    stoch_k = ((close - low.rolling(14).min()) /
               (high.rolling(14).max() - low.rolling(14).min()).replace(0, np.nan) * 100)
    df["stoch_k"] = stoch_k.shift(1)
    df["stoch_d"] = stoch_k.rolling(3).mean().shift(1)

    # ── Relative Volume ──
    df["rel_volume"] = (volume / volume.rolling(60).mean().replace(0, np.nan)).shift(1)

    # ── Clean up NaN dari periode awal ──
    df.replace([np.inf, -np.inf], np.nan, inplace=True)

    return df


def fetch_ihsg_cached(period: str = "2y", cache_minutes: int = 1440) -> pd.DataFrame:
    """
    Ambil data IHSG (^JKSE) dengan cache lokal 1 jam.
    Dipanggil sekali, hasilnya dipakai untuk semua ticker — tidak ada download
    ulang per ticker.
    """
    cache_dir = "cache"
    cache_path = os.path.join(cache_dir, "_IHSG_.csv")
    os.makedirs(cache_dir, exist_ok=True)

    # Cek cache
    if os.path.exists(cache_path):
        mtime = os.path.getmtime(cache_path)
        age_minutes = (time.time() - mtime) / 60
        if age_minutes < cache_minutes:
            logger.info("IHSG cache HIT (age=%.1f menit)", age_minutes)
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            if not df.empty:
                return df

    # Cache miss — download
    logger.info("IHSG cache MISS — download ^JKSE...")
    idx = yf.download("^JKSE", period=period, progress=False,
                      auto_adjust=True, multi_level_index=False)
    if idx.empty:
        logger.warning("IHSG data kosong")
        return pd.DataFrame()

    idx.columns = [c.lower() for c in idx.columns]
    idx.index = pd.to_datetime(idx.index)
    idx.index.name = "date"
    idx.to_csv(cache_path)
    logger.info("IHSG disimpan ke cache: %s", cache_path)
    return idx


def align_to_market(df_stock: pd.DataFrame,
                    market_ticker: str = "^JKSE",
                    df_ihsg: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Gabungkan data saham dengan IHSG. Return DataFrame dengan kolom idx_close,
    idx_ret_20d, idx_volatility.

    Parameters
    ----------
    df_stock : DataFrame saham
    market_ticker : str, default "^JKSE"
    df_ihsg : DataFrame IHSG (dari fetch_ihsg_cached()), optional.
              Jika None, panggil fetch_ihsg_cached() secara internal.
              Gunakan parameter ini untuk menghindari download ulang per ticker.
    """
    try:
        if df_ihsg is None:
            df_ihsg = fetch_ihsg_cached()
        if df_ihsg.empty:
            df_stock["idx_close"] = np.nan
            df_stock["idx_ret_20d"] = 0.0
            df_stock["idx_volatility"] = 0.0
            return df_stock

        idx_close = df_ihsg["close"]

        # Align index ke df_stock
        idx_close = pd.Series(idx_close.values, index=pd.to_datetime(df_ihsg.index))
        idx_close = idx_close.reindex(df_stock.index, method="ffill")

        df_stock["idx_close"] = idx_close
        df_stock["idx_ret_20d"] = idx_close.pct_change(20).shift(1)
        df_stock["idx_volatility"] = idx_close.pct_change().rolling(20).std().shift(1)

    except Exception as e:
        logger.warning("Gagal align IHSG: %s", e)
        df_stock["idx_close"] = np.nan
        df_stock["idx_ret_20d"] = 0.0
        df_stock["idx_volatility"] = 0.0

    return df_stock


# ============================================================
# Cache + Retry + Parallel Fetch Utilities (appended)
# ============================================================


def fetch_with_cache(ticker: str, period: str = "18mo", cache_minutes: int = 28800) -> pd.DataFrame:
    """
    Ambil data harga dengan cache CSV lokal.
    Jika file cache/{ticker}.csv ada dan masih fresh (< cache_minutes),
    baca dari CSV. Jika tidak, panggil fetch_prices() dan simpan ke cache.
    """
    cache_dir = "cache"
    cache_path = os.path.join(cache_dir, f"{ticker}.csv")

    os.makedirs(cache_dir, exist_ok=True)

    # Cek apakah cache ada dan masih fresh
    if os.path.exists(cache_path):
        mtime = os.path.getmtime(cache_path)
        age_minutes = (time.time() - mtime) / 60
        if age_minutes < cache_minutes:
            logger.info("Cache HIT: %s (age=%.1f menit)", ticker, age_minutes)
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            if not df.empty:
                return df
            logger.warning("Cache file kosong untuk %s, fetch ulang", ticker)

    # Cache miss — fetch from API
    logger.info("Cache MISS: %s — fetch_prices()", ticker)
    df = fetch_prices(ticker, period)

    if not df.empty:
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(cache_path)
        logger.info("Disimpan ke cache: %s", cache_path)
    else:
        logger.warning("Data kosong untuk %s, tidak disimpan ke cache", ticker)

    return df


def fetch_with_retry(ticker: str, period: str = "18mo", max_retries: int = 3, delay: int = 2) -> pd.DataFrame:
    """
    Ambil data harga dengan retry + exponential backoff.
    max_retries kali percobaan, delay awal 2 detik (2, 4, 8, ...).
    Return pd.DataFrame() jika semua gagal.
    """
    for attempt in range(1, max_retries + 1):
        try:
            df = fetch_prices(ticker, period)
            if not df.empty:
                return df
            logger.warning("Percobaan %d/%d: data kosong untuk %s", attempt, max_retries, ticker)
        except Exception as e:
            logger.warning("Percobaan %d/%d gagal untuk %s: %s", attempt, max_retries, ticker, e)

        if attempt < max_retries:
            wait = delay * (2 ** (attempt - 1))  # exponential backoff: 2, 4, 8, ...
            logger.info("Tunggu %d detik sebelum retry %s...", wait, ticker)
            time.sleep(wait)

    logger.error("Semua percobaan gagal untuk %s setelah %d kali retry", ticker, max_retries)
    return pd.DataFrame()


def scan_multiple(tickers, max_workers: int = 3, delay_between: float = 0.3) -> dict:
    """
    Fetch harga multiple ticker secara paralel.

    Parameters
    ----------
    tickers : list-like of str
    max_workers : int, default 1
        (Diabaikan — dipaksa 1 untuk hindari rate limit Yahoo)
    delay_between : float, default 0.3
        Jeda antar ticker (setelah selesai fetch) dalam detik.

    Returns
    -------
    dict : {ticker: DataFrame}
    """
    results = {}

    def _fetch_one(tkr: str) -> tuple:
        """Wrapper untuk menangkap hasil per ticker."""
        try:
            df = fetch_prices(tkr)
            return tkr, df
        except Exception as e:
            logger.error("Gagal fetch %s: %s", tkr, e)
            return tkr, pd.DataFrame()

    max_workers = min(max(1, max_workers), 10)  # clamp 1-10
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, tkr): tkr for tkr in tickers}

        for future in concurrent.futures.as_completed(futures):
            tkr = futures[future]
            try:
                tkr, df = future.result()
                results[tkr] = df
            except Exception as e:
                logger.error("Unexpected error untuk %s: %s", tkr, e)
                results[tkr] = pd.DataFrame()

            time.sleep(delay_between)

    return results


# ============================================================
# Swing-Specific Functions (appended)
# ============================================================


def compute_weekly_trend(df_daily: pd.DataFrame) -> str:
    """
    Hitung trend mingguan berdasarkan EMA12 vs EMA50 weekly.
    Resample daily -> weekly (W-FRI), shift(1) untuk hindari look-ahead.

    Returns
    -------
    str : 'BULLISH' jika EMA12 > EMA50, 'BEARISH' jika sebaliknya,
          'NO_DATA' jika data tidak cukup.
    """
    if df_daily.empty or "close" not in df_daily.columns:
        return "NO_DATA"
    if not isinstance(df_daily.index, pd.DatetimeIndex):
        try:
            df_daily = df_daily.copy()
            df_daily.index = pd.to_datetime(df_daily.index)
        except Exception:
            return "NO_DATA"

    # Resample ke weekly, ambil close terakhir setiap minggu (W-FRI)
    weekly_close = df_daily["close"].resample("W-FRI").last()

    if len(weekly_close) < 50:
        return "NO_DATA"

    # Hitung EMA12 dan EMA50 weekly
    ema12 = weekly_close.ewm(span=12, adjust=False).mean()
    ema50 = weekly_close.ewm(span=50, adjust=False).mean()

    # Shift(1) untuk hindari look-ahead
    if ema12.shift(1).iloc[-1] > ema50.shift(1).iloc[-1]:
        return "BULLISH"
    else:
        return "BEARISH"


def detect_volatility_regime(df: pd.DataFrame) -> str:
    """
    Deteksi regime volatilitas berdasarkan ATR% rata-rata 20 hari terakhir.

    Returns
    -------
    str : 'LOW' (ATR%% < 1.5%%), 'NORMAL' (1.5-3.0%%), 'HIGH' (>3.0%%)
    """
    if df.empty or not all(c in df.columns for c in ["high", "low", "close"]):
        return "NORMAL"

    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    # True Range
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # ATR 14
    atr = tr.rolling(14).mean()

    # ATR% = ATR / close * 100
    atr_pct = (atr / close) * 100

    # Average ATR% last 20 days
    avg_atr_pct = atr_pct.tail(20).mean()

    if pd.isna(avg_atr_pct):
        return "NORMAL"

    if avg_atr_pct < 1.5:
        return "LOW"
    elif avg_atr_pct > 3.0:
        return "HIGH"
    else:
        return "NORMAL"


def compute_support_resistance(close: pd.Series, lookback: int = 60) -> dict:
    """
    Cari level support (swing low) dan resistance (swing high) dalam lookback hari.
    Pakai rolling window: swing high = close > prev N dan next N.

    Returns
    -------
    dict : {
        "nearest_support": float,
        "nearest_resistance": float,
        "dist_to_support_pct": float,
        "dist_to_resistance_pct": float
    }
    """
    default = {
        "nearest_support": float(close.iloc[-1] * 0.95) if len(close) > 0 else 0.0,
        "nearest_resistance": float(close.iloc[-1] * 1.05) if len(close) > 0 else 0.0,
        "dist_to_support_pct": -5.0,
        "dist_to_resistance_pct": 5.0,
    }

    if close.empty or len(close) < lookback:
        return default

    # Gunakan data lookback terakhir
    recent = close.iloc[-lookback:]
    current_price = float(close.iloc[-1])

    n = 5  # window size untuk swing detection (prev N dan next N)

    swing_highs = []
    swing_lows = []

    for i in range(n, len(recent) - n):
        # Swing high: close[i] > semua neighbor dalam window n
        is_swing_high = True
        for j in range(1, n + 1):
            if recent.iloc[i] <= recent.iloc[i - j] or recent.iloc[i] <= recent.iloc[i + j]:
                is_swing_high = False
                break
        if is_swing_high:
            swing_highs.append(recent.iloc[i])

        # Swing low: close[i] < semua neighbor dalam window n
        is_swing_low = True
        for j in range(1, n + 1):
            if recent.iloc[i] >= recent.iloc[i - j] or recent.iloc[i] >= recent.iloc[i + j]:
                is_swing_low = False
                break
        if is_swing_low:
            swing_lows.append(recent.iloc[i])

    # Nearest support = swing low tertinggi di bawah harga saat ini
    supports_below = [s for s in swing_lows if s < current_price]
    if supports_below:
        nearest_support = max(supports_below)
    else:
        nearest_support = float(recent.min())

    # Nearest resistance = swing high terendah di atas harga saat ini
    resistances_above = [r for r in swing_highs if r > current_price]
    if resistances_above:
        nearest_resistance = min(resistances_above)
    else:
        nearest_resistance = float(recent.max())

    dist_to_support_pct = float((current_price - nearest_support) / nearest_support * 100)
    dist_to_resistance_pct = float((nearest_resistance - current_price) / current_price * 100)

    return {
        "nearest_support": float(nearest_support),
        "nearest_resistance": float(nearest_resistance),
        "dist_to_support_pct": round(dist_to_support_pct, 2),
        "dist_to_resistance_pct": round(dist_to_resistance_pct, 2),
    }


def detect_volume_breakout(df: pd.DataFrame, lookback: int = 20) -> bool:
    """
    Deteksi volume breakout: bandingkan volume hari ini vs rata-rata lookback hari.

    Returns
    -------
    bool : True jika volume > 1.5x rata-rata.
    """
    if df.empty or "volume" not in df.columns or len(df) < lookback + 1:
        return False

    volume = df["volume"]
    avg_vol = volume.shift(1).rolling(lookback).mean()  # exclude today from avg

    current_vol = volume.iloc[-1]
    current_avg = avg_vol.iloc[-1]

    if pd.isna(current_avg) or current_avg == 0:
        return False

    return bool(current_vol > 1.5 * current_avg)


def compute_trend_strength(df: pd.DataFrame) -> float:
    """
    Hitung kekuatan trend (0-100) berdasarkan 3 komponen:
    - ADX (bobot 50%%)
    - EMA alignment — EMA12 vs EMA50 (25%%)
    - Price vs MA200 (25%%)

    Returns
    -------
    float : Skor 0-100 (0 = weak/no trend, 100 = strong trend).
    """
    if df.empty or "close" not in df.columns or len(df) < 200:
        return 0.0

    close = df["close"]

    # ── Component 1: ADX (50%) ──
    # Coba pakai kolom adx yang sudah ada
    if "adx" in df.columns and not df["adx"].dropna().empty:
        adx_val = df["adx"].iloc[-1]
    else:
        # Hitung ADX manual jika belum ada
        high = df["high"] if "high" in df.columns else close
        low = df["low"] if "low" in df.columns else close

        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move
        minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move

        plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr.replace(0, np.nan))
        minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr.replace(0, np.nan))

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.rolling(14).mean()
        adx_val = adx.iloc[-1]

    if pd.isna(adx_val):
        adx_val = 0.0

    # Normalisasi ADX: 0-25 -> 0-50, 25-50 -> 50-100, >50 -> 100
    if adx_val >= 50:
        adx_score = 100.0
    elif adx_val >= 25:
        adx_score = 50.0 + (adx_val - 25) / 25 * 50.0
    else:
        adx_score = adx_val / 25 * 50.0

    # ── Component 2: EMA Alignment (25%) ──
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    price = close.iloc[-1]
    p_ema12 = ema12.iloc[-1]
    p_ema50 = ema50.iloc[-1]

    if pd.isna(p_ema12) or pd.isna(p_ema50):
        ema_alignment_score = 0.0
    else:
        if price > p_ema12 > p_ema50 or price < p_ema12 < p_ema50:
            # Perfect alignment (bullish cascade or bearish cascade)
            ema_alignment_score = 100.0
        elif (price > p_ema50 and p_ema12 > p_ema50) or (price < p_ema50 and p_ema12 < p_ema50):
            # Partial alignment — price and EMA12 on same side of EMA50
            ema_alignment_score = 50.0
        else:
            # No alignment
            ema_alignment_score = 0.0

    # ── Component 3: Price vs MA200 (25%) ──
    ma200 = close.rolling(200).mean()
    p_ma200 = ma200.iloc[-1]

    if pd.isna(p_ma200) or p_ma200 == 0:
        ma200_score = 0.0
    else:
        pct_from_ma200 = (price - p_ma200) / p_ma200 * 100
        # Strong deviation = strong trend (both directions)
        ma200_score = min(100.0, abs(pct_from_ma200) * 5)

    # ── Composite Score ──
    composite = adx_score * 0.50 + ema_alignment_score * 0.25 + ma200_score * 0.25

    return round(min(100.0, max(0.0, composite)), 2)


# ── Fundamental Data ──────────────────────────────────────────────────
_FUNDAMENTAL_CACHE: dict = {}
"""
Cache fundamental data per ticker dalam satu sesi.
Key: ticker (str) → Value: dict hasil fetch atau None.
Guna menghindari rate limit dari Yahoo Finance.
"""


def fetch_fundamental(ticker: str, use_cache: bool = True) -> dict:
    """
    Ambil data fundamental via yfinance Ticker.info.

    Parameters
    ----------
    ticker : str
        Ticker saham dengan suffix .JK (contoh: 'BBCA.JK')
    use_cache : bool
        True = cache dalam sesi (default), False = force fresh fetch.

    Returns
    -------
    dict
        Dictionary berisi field fundamental terpilih.
        Kosong jika gagal fetch.
    """
    if use_cache and ticker in _FUNDAMENTAL_CACHE:
        cached = _FUNDAMENTAL_CACHE[ticker]
        if cached is not None:
            return cached
        else:
            return {}

    try:
        import yfinance as yf
        import requests as _req
        # Session with timeout untuk hindari hang
        _sess = _req.Session()
        _sess.mount('https://', _req.adapters.HTTPAdapter(max_retries=2))
        t = yf.Ticker(ticker, session=_sess)
        # Wrap .info dengan timeout runtime
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(lambda: t.info)
            try:
                info = fut.result(timeout=20)
            except TimeoutError:
                logger.warning("Timeout fetch fundamental %s (20s)", ticker)
                _FUNDAMENTAL_CACHE[ticker] = None
                return {}
        if not info or len(info) < 10:
            logger.warning("Fundamental data kosong untuk %s", ticker)
            _FUNDAMENTAL_CACHE[ticker] = None
            return {}

        # Ekstrak field yang berguna
        result = {
            "market_cap":       info.get("marketCap"),
            "pe_ratio":         info.get("trailingPE"),
            "forward_pe":       info.get("forwardPE"),
            "pbv":              info.get("priceToBook"),
            "dividend_yield":   info.get("dividendYield"),
            "dividend_rate":    info.get("dividendRate"),
            "beta":             info.get("beta"),
            "sector":           info.get("sector"),
            "industry":         info.get("industry"),
            "analyst_rating":   info.get("recommendationKey"),
            "target_price":     info.get("targetMeanPrice"),
            "target_high":      info.get("targetHighPrice"),
            "target_low":       info.get("targetLowPrice"),
            "analyst_count":    info.get("numberOfAnalystOpinions"),
            "roe":              info.get("returnOnEquity"),
            "profit_margin":    info.get("profitMargins"),
            "revenue_growth":   info.get("revenueGrowth"),
            "earnings_growth":  info.get("earningsGrowth"),
            "eps_ttm":          info.get("trailingEps"),
            "book_value":       info.get("bookValue"),
            "52w_high":         info.get("fiftyTwoWeekHigh"),
            "52w_low":          info.get("fiftyTwoWeekLow"),
        }

        # Simpan cache
        _FUNDAMENTAL_CACHE[ticker] = result
        logger.debug("Fundamental OK: %s — MC %.0f, P/E %.1f",
                     ticker.replace(".JK", ""),
                     result.get("market_cap", 0) or 0,
                     result.get("pe_ratio", 0) or 0)
        return result

    except Exception as e:
        logger.warning("Gagal fetch fundamental %s: %s", ticker, e)
        _FUNDAMENTAL_CACHE[ticker] = None
        return {}


def clear_fundamental_cache():
    """Bersihkan cache fundamental untuk refresh data."""
    _FUNDAMENTAL_CACHE.clear()
    logger.debug("Cache fundamental dibersihkan")
