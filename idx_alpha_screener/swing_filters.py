"""
swing_filters.py — Mandatory pre-signal filters: Weekly Trend Alignment + Volume Breakout
========================================================================================

Module ini berisi filter wajib (hard gate) yang harus LULUS sebelum saham bisa
menghasilkan sinyal BUY apapun. Threshold scoring diturunkan ke >=55, jadi filter
ini yang menjaga kualitas sinyal.

Fungsi Publik:
--------------
- weekly_trend_alignment(df_daily, ema_short=20, ema_long=50) -> bool
    Resample daily ke weekly, cek EMA20 & EMA50 alignment.

- volume_breakout(df_daily, multiplier=1.5, lookback=20) -> bool
    Cek volume hari ini > multiplier x avg_volume lookback hari, plus close > open.

- swing_gate_pass(df_daily) -> dict
    Gabungan kedua filter di atas dalam satu fungsi.
    Return dict: {'passed': bool, 'trend_aligned': bool, 'volume_breakout': bool, 'reasons': list}

Contoh:
-------
    result = swing_gate_pass(df)
    if not result['passed']:
        logger.info(f"{ticker}: swing gate ditolak — {', '.join(result['reasons'])}")
        continue  # skip saham ini
"""

from __future__ import annotations

import logging
from typing import Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
#  1. WEEKLY TREND ALIGNMENT
# ─────────────────────────────────────────────────────────────────────

def weekly_trend_alignment(
    df_daily: pd.DataFrame,
    ema_short: int = 20,
    ema_long: int = 50,
) -> bool:
    """
    Cek apakah weekly trend searah dengan sinyal BUY menggunakan EMA.

    Logic:
    ------
    1. Resample data daily ke weekly (akhir pekan Jumat: 'W-FRI').
    2. Hitung EMA20 dan EMA50 dari harga close weekly.
    3. Return True jika KETIGA kondisi berikut terpenuhi:
        - close_weekly[-1] > ema20_weekly[-1]   (harga di atas EMA20 mingguan)
        - close_weekly[-1] > ema50_weekly[-1]   (harga di atas EMA50 mingguan)
        - ema20_weekly[-1] > ema50_weekly[-1]   (uptrend, EMA20 > EMA50)

    Parameters
    ----------
    df_daily : pd.DataFrame
        DataFrame harian dengan kolom 'close' (atau 'Close').
        Index harus pd.DatetimeIndex.
    ema_short : int, optional
        Periode EMA cepat (default 20).
    ema_long : int, optional
        Periode EMA lambat (default 50).

    Returns
    -------
    bool
        True jika weekly trend bullish (aligned untuk BUY), False jika tidak.

    Contoh
    ------
    >>> aligned = weekly_trend_alignment(df)
    >>> print(aligned)
    True
    """
    # ── Guard: data cukup ──
    if df_daily is None or df_daily.empty:
        logger.warning("weekly_trend_alignment: DataFrame kosong atau None")
        return False

    if len(df_daily) < 40:
        logger.warning(
            "weekly_trend_alignment: data tidak cukup (%d baris, butuh >=40)",
            len(df_daily),
        )
        return False

    # ── Normalisasi kolom close ──
    close_col = _resolve_close_column(df_daily)
    if close_col is None:
        logger.warning("weekly_trend_alignment: tidak ada kolom close/Close")
        return False

    # ── Pastikan index DatetimeIndex ──
    df = df_daily.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception as e:
            logger.warning("weekly_trend_alignment: gagal konversi index ke DatetimeIndex: %s", e)
            return False

    # ── Resolve all column names ──
    open_col = _resolve_open_column(df)
    high_col = _resolve_high_column(df)
    low_col = _resolve_low_column(df)
    vol_col = _resolve_volume_column(df)

    # ── Resample ke weekly (W-FRI) ──
    agg_dict: Dict[str, str] = {}
    agg_dict[close_col] = "last"
    if open_col:
        agg_dict[open_col] = "first"
    if high_col:
        agg_dict[high_col] = "max"
    if low_col:
        agg_dict[low_col] = "min"
    if vol_col:
        agg_dict[vol_col] = "sum"

    try:
        df_weekly = df.resample("W-FRI").agg(agg_dict)
    except Exception as e:
        logger.warning("weekly_trend_alignment: gagal resample: %s", e)
        return False

    # Cek data cukup setelah resample
    if df_weekly.empty or df_weekly[close_col].isna().all():
        return False

    # Drop baris dengan NaN di close
    df_weekly = df_weekly.dropna(subset=[close_col])

    if len(df_weekly) < ema_long - 10:
        logger.warning(
            "weekly_trend_alignment: baris weekly tidak cukup (%d, butuh >=%d)",
            len(df_weekly), ema_long - 10,
        )
        return False

    # ── Hitung EMA (vectorized) ──
    close_w = df_weekly[close_col]
    ema20 = close_w.ewm(span=ema_short, adjust=False).mean()
    ema50 = close_w.ewm(span=ema_long, adjust=False).mean()

    # ── Cek 3 kondisi ──
    last_close = close_w.iloc[-1]
    last_ema20 = ema20.iloc[-1]
    last_ema50 = ema50.iloc[-1]

    cond1 = last_close > last_ema20
    cond2 = last_close > last_ema50
    cond3 = last_ema20 > last_ema50

    passed = bool(cond1 and cond2 and cond3)

    if not passed:
        logger.debug(
            "weekly_trend_alignment: GAGAL — close=%.2f, ema20=%.2f, ema50=%.2f | "
            "cond1(close>ema20)=%s, cond2(close>ema50)=%s, cond3(ema20>ema50)=%s",
            last_close, last_ema20, last_ema50,
            cond1, cond2, cond3,
        )

    return passed


# ─────────────────────────────────────────────────────────────────────
#  2. VOLUME BREAKOUT
# ─────────────────────────────────────────────────────────────────────

def volume_breakout(
    df_daily: pd.DataFrame,
    multiplier: float = 1.2,
    lookback: int = 20,
) -> bool:
    """
    Deteksi volume breakout harian.

    Logic:
    ------
    1. Hitung rata-rata volume 'lookback' hari terakhir (hari ini TIDAK termasuk).
    2. Cek volume hari ini > multiplier x avg_volume.
    3. Cek close > open (harga naik, konfirmasi breakout).
    4. Return True hanya jika KEDUA kondisi terpenuhi.

    Parameters
    ----------
    df_daily : pd.DataFrame
        DataFrame harian dengan kolom 'volume', 'close', 'open' (atau 'Volume',
        'Close', 'Open'). Index harus pd.DatetimeIndex.
    multiplier : float, optional
        Faktor pengali rata-rata volume (default 1.2).
    lookback : int, optional
        Jumlah hari untuk rata-rata volume, tidak termasuk hari ini (default 20).

    Returns
    -------
    bool
        True jika volume breakout terkonfirmasi, False jika tidak.

    Contoh
    ------
    >>> breakout = volume_breakout(df)
    >>> print(breakout)
    True
    """
    # ── Guard: data cukup ──
    if df_daily is None or df_daily.empty:
        logger.warning("volume_breakout: DataFrame kosong atau None")
        return False

    if len(df_daily) < lookback + 1:
        logger.warning(
            "volume_breakout: data tidak cukup (%d baris, butuh >=%d)",
            len(df_daily), lookback + 1,
        )
        return False

    # ── Normalisasi kolom ──
    close_col = _resolve_close_column(df_daily)
    open_col = _resolve_open_column(df_daily)
    vol_col = _resolve_volume_column(df_daily)

    if close_col is None or open_col is None or vol_col is None:
        logger.warning("volume_breakout: kolom yang dibutuhkan tidak ditemukan")
        return False

    # ── Validasi volume hari ini ──
    last_vol = df_daily[vol_col].iloc[-1]
    if pd.isna(last_vol) or last_vol <= 0:
        logger.warning("volume_breakout: volume hari ini NaN atau <= 0")
        return False

    # ── Cek close > open (konfirmasi harga naik) ──
    last_close = df_daily[close_col].iloc[-1]
    last_open = df_daily[open_col].iloc[-1]

    if pd.isna(last_close) or pd.isna(last_open):
        logger.warning("volume_breakout: close/open hari ini NaN")
        return False

    price_up = last_close > last_open
    if not price_up:
        logger.debug(
            "volume_breakout: GAGAL — close (%.2f) tidak > open (%.2f)",
            last_close, last_open,
        )
        return False

    # ── Hitung rata-rata volume lookback hari (exclude hari ini) ──
    vol_series = df_daily[vol_col].iloc[-(lookback + 1):-1]  # exclude last
    avg_volume = vol_series.mean()

    if pd.isna(avg_volume) or avg_volume <= 0:
        logger.warning("volume_breakout: rata-rata volume NaN atau <= 0")
        return False

    # ── Cek volume > multiplier x avg ──
    vol_ok = last_vol > multiplier * avg_volume

    if not vol_ok:
        logger.debug(
            "volume_breakout: GAGAL — vol=%.0f, threshold=%.0f (%.1f x %.0f)",
            last_vol, multiplier * avg_volume, multiplier, avg_volume,
        )

    return bool(vol_ok)


# ─────────────────────────────────────────────────────────────────────
#  3. SWING GATE PASS (ORCHESTRATOR)
# ─────────────────────────────────────────────────────────────────────

def multi_timeframe_confirm(df_daily: pd.DataFrame, 
                              ema_short_daily: int = 12,
                              ema_long_daily: int = 50) -> bool:
    """
    P1: Multi-Timeframe Confirmation.
    
    Cek apakah daily DAN weekly timeframe sama-sama uptrend.
    Ini mencegah false signal dari daily bounce di weekly downtrend.
    
    Logic:
    - Weekly: close > EMA20 > EMA50 (uptrend weekly)
    - Daily: close > EMA12 > EMA50 (uptrend daily)
    - Return True jika KEDUA timeframe uptrend
    
    Parameters
    ----------
    df_daily : pd.DataFrame
        DataFrame harian dengan kolom 'close'.
    ema_short_daily : int
        EMA cepat harian (default 12).
    ema_long_daily : int
        EMA lambat harian (default 50).
    
    Returns
    -------
    bool
        True jika daily + weekly sama-sama uptrend.
    """
    # ── Cek weekly uptrend ──
    weekly_ok = weekly_trend_alignment(df_daily)
    if not weekly_ok:
        return False
    
    # ── Cek daily uptrend ──
    close_col = _resolve_close_column(df_daily)
    if close_col is None:
        return False
    
    close = df_daily[close_col]
    ema12 = close.ewm(span=ema_short_daily, adjust=False).mean()
    ema50 = close.ewm(span=ema_long_daily, adjust=False).mean()
    
    last_close = close.iloc[-1]
    last_ema12 = ema12.iloc[-1]
    last_ema50 = ema50.iloc[-1]
    
    daily_uptrend = last_close > last_ema12 > last_ema50
    return daily_uptrend


def swing_gate_pass(df_daily: pd.DataFrame) -> Dict[str, any]:
    """
    Gabungan filter weekly trend alignment + volume breakout sebagai mandatory gate.

    Kedua filter harus LULUS (return True) agar 'passed' = True.
    Jika salah satu atau keduanya gagal, 'reasons' akan berisi daftar alasan.

    Parameters
    ----------
    df_daily : pd.DataFrame
        DataFrame harian dengan kolom OHLCV standar (open, high, low, close, volume).
        Index harus pd.DatetimeIndex.

    Returns
    -------
    dict
        Dictionary dengan keys:
        - passed (bool)        : True hanya jika KEDUA filter lulus
        - trend_aligned (bool) : hasil weekly_trend_alignment()
        - volume_breakout (bool): hasil volume_breakout()
        - reasons (list[str])  : daftar alasan jika gagal (kosong jika passed)

    Contoh
    ------
    >>> result = swing_gate_pass(df)
    >>> if not result['passed']:
    ...     print(f"Ditolak: {', '.join(result['reasons'])}")
    """
    result: Dict[str, any] = {
        "passed": False,
        "trend_aligned": False,
        "volume_breakout": False,
        "reasons": [],
    }

    # ── Guard: data valid ──
    if df_daily is None or df_daily.empty:
        result["reasons"].append("DataFrame kosong atau None")
        return result

    # ── Filter 1: Weekly Trend Alignment ──
    try:
        trend_ok = weekly_trend_alignment(df_daily)
        result["trend_aligned"] = trend_ok
        if not trend_ok:
            result["reasons"].append(
                "Weekly trend tidak aligned (butuh close>ema20>ema50 weekly)"
            )
    except Exception as e:
        logger.error("swing_gate_pass: error weekly_trend_alignment: %s", e)
        result["reasons"].append(f"Weekly trend error: {e}")

    # ── Filter 2: Volume Breakout ──
    try:
        vol_ok = volume_breakout(df_daily)
        result["volume_breakout"] = vol_ok
        if not vol_ok:
            result["reasons"].append(
                "Volume tidak breakout (butuh >1.2x avg 20 hari + close>open)"
            )
    except Exception as e:
        logger.error("swing_gate_pass: error volume_breakout: %s", e)
        result["reasons"].append(f"Volume breakout error: {e}")

    # ── Gate: KEDUA harus lulus ──
    if result["trend_aligned"] and result["volume_breakout"]:
        result["passed"] = True

    return result


# ─────────────────────────────────────────────────────────────────────
#  HELPER: Normalisasi kolom
# ─────────────────────────────────────────────────────────────────────

def _resolve_close_column(df: pd.DataFrame) -> str | None:
    """Cari kolom close ('close' atau 'Close')."""
    for col in ("close", "Close"):
        if col in df.columns:
            return col
    return None


def _resolve_open_column(df: pd.DataFrame) -> str | None:
    """Cari kolom open ('open' atau 'Open')."""
    for col in ("open", "Open"):
        if col in df.columns:
            return col
    return None


def _resolve_volume_column(df: pd.DataFrame) -> str | None:
    """Cari kolom volume ('volume' atau 'Volume')."""
    for col in ("volume", "Volume"):
        if col in df.columns:
            return col
    return None


def _resolve_high_column(df: pd.DataFrame) -> str | None:
    """Cari kolom high ('high' atau 'High')."""
    for col in ("high", "High"):
        if col in df.columns:
            return col
    return None


def _resolve_low_column(df: pd.DataFrame) -> str | None:
    """Cari kolom low ('low' atau 'Low')."""
    for col in ("low", "Low"):
        if col in df.columns:
            return col
    return None


# ─────────────────────────────────────────────────────────────────────
#  UNIT TEST / VALIDATION
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    print("=" * 65)
    print("  swing_filters.py — Self-Test / Validasi")
    print("=" * 65)

    # ── Generate synthetic daily data (400 hari) ──
    np.random.seed(42)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=400, freq="B")

    # Simulasi uptrend dengan pullback dan breakout di akhir
    close = 100.0 + np.cumsum(np.random.randn(400) * 0.5)
    # Pastikan uptrend: tambahkan drift
    close += np.linspace(0, 30, 400)

    open_p = close + np.random.randn(400) * 0.3
    high = np.maximum(close, open_p) + np.abs(np.random.randn(400) * 0.3)
    low = np.minimum(close, open_p) - np.abs(np.random.randn(400) * 0.3)

    # Volume: normal, lalu spike di akhir
    vol = np.random.randint(1_000_000, 3_000_000, 400).astype(float)
    vol[-1] = 8_000_000  # breakout volume
    # Pastikan candle terakhir bullish
    open_p[-1] = close[-1] - 2.0
    close[-1] = open_p[-1] + 3.5
    high[-1] = close[-1] + 1.0
    low[-1] = open_p[-1] - 0.5

    df = pd.DataFrame(
        {"open": open_p, "high": high, "low": low, "close": close, "volume": vol},
        index=dates,
    )

    print(f"\nData shape: {df.shape}")
    print(f"Date range: {df.index[0].date()} → {df.index[-1].date()}")
    print(f"Last close: {df['close'].iloc[-1]:.2f}, Last open: {df['open'].iloc[-1]:.2f}")
    print(f"Last volume: {df['volume'].iloc[-1]:,.0f}")

    # ── Test 1: weekly_trend_alignment ──
    print("\n─── Test 1: weekly_trend_alignment ───")
    trend_ok = weekly_trend_alignment(df)
    print(f"  Result: {trend_ok}")
    assert isinstance(trend_ok, bool), "Must return bool"

    # ── Test 2: volume_breakout ──
    print("\n─── Test 2: volume_breakout ───")
    vol_ok = volume_breakout(df)
    print(f"  Result: {vol_ok}")
    assert isinstance(vol_ok, bool), "Must return bool"

    # ── Test 3: swing_gate_pass ──
    print("\n─── Test 3: swing_gate_pass ───")
    gate = swing_gate_pass(df)
    print(f"  passed:          {gate['passed']}")
    print(f"  trend_aligned:   {gate['trend_aligned']}")
    print(f"  volume_breakout: {gate['volume_breakout']}")
    print(f"  reasons:         {gate['reasons']}")
    assert isinstance(gate, dict)
    assert "passed" in gate
    assert "trend_aligned" in gate
    assert "volume_breakout" in gate
    assert "reasons" in gate

    # ── Test 4: edge cases ──
    print("\n─── Test 4: Edge cases ───")

    # 4a. DataFrame kosong
    empty_df = pd.DataFrame()
    res = swing_gate_pass(empty_df)
    print(f"  Empty DF passed={res['passed']} reasons={res['reasons']}")
    assert res["passed"] is False
    assert len(res["reasons"]) > 0

    # 4b. Data tidak cukup (< 60 baris)
    small_df = df.iloc[:30]
    res = weekly_trend_alignment(small_df)
    print(f"  Small DF (<60 baris) trend_alignment={res}")
    assert res is False

    # 4c. Data tidak cukup untuk volume (< 21 baris)
    tiny_df = df.iloc[:15]
    res = volume_breakout(tiny_df)
    print(f"  Tiny DF (<21 baris) volume_breakout={res}")
    assert res is False

    # 4d. Volume NaN
    df_nan_vol = df.copy()
    df_nan_vol.loc[df_nan_vol.index[-1], "volume"] = np.nan
    res = volume_breakout(df_nan_vol)
    print(f"  Volume NaN: {res}")
    assert res is False

    # 4e. Close <= Open (bearish candle)
    df_bear = df.copy()
    df_bear.loc[df_bear.index[-1], "close"] = df_bear["open"].iloc[-1] - 1.0
    res = volume_breakout(df_bear)
    print(f"  Bearish candle (close<open): {res}")
    assert res is False

    # ── Test 5: Case sensitivity ──
    print("\n─── Test 5: Kolom uppercase ───")
    df_up = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    res = swing_gate_pass(df_up)
    print(f"  Uppercase columns passed={res['passed']}")
    # Should work with uppercase columns too

    print("\n" + "=" * 65)
    print("  SEMUA TEST SELESAI ✅" if gate["passed"] else "  TEST SELESAI (beberapa filter mungkin gagal sesuai skenario)")
    print("=" * 65)
