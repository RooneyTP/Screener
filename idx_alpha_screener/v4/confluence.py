"""
v4/confluence.py — Confluence Gate
====================================
Menggantikan swing_gate_pass() dari swing_filters.py di mode v4.

Cara kerja:
  Alih-alih binary pass/fail (butuh trend_aligned AND volume_breakout),
  Confluence Gate menghitung berapa banyak konfirmasi dari berbagai
  sumber yang saling menguatkan (confluence).

  Skor confluence: 0-100
    - 0-20:  hampir tidak ada konfirmasi
    - 21-40: konfirmasi minimal
    - 41-60: konfirmasi cukup
    - 61-80: konfirmasi kuat
    - 81-100: konfirmasi sangat kuat

Perbedaan dari swing_gate_pass v3:
  v3: trend_aligned AND volume_breakout → pass (binary)
      trend_aligned OR volume_breakout → partial pass
      none → fail
  
  v4: hitung confluence dari 6 sumber:
      1. Daily trend alignment
      2. Weekly trend alignment
      3. Volume breakout
      4. OBV trend
      5. Donchian breakout
      6. Price vs EMA50
      Masing-masing → confluence contribution
      Total → digunakan sebagai modifier conviction
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger("v4.confluence")


def score_confluence(row: pd.Series) -> dict:
    """
    Hitung confluence score dari berbagai sumber konfirmasi.

    Parameters
    ----------
    row : pd.Series
        Baris data dengan indikator yang sudah dihitung.

    Returns
    -------
    dict dengan keys:
        confluence : float (0-100)
        signals : dict {name: bool} — sumber konfirmasi
        strengths : dict {name: float} — kekuatan per sumber (0-100)
        detail : str
    """
    signals = {}
    strengths = {}
    total = 0.0
    count = 0

    # ── 1. Daily Trend Alignment ──
    ema12 = row.get("ema12", 0)
    ema50 = row.get("ema50", 0)
    price = row.get("close", 0)
    adx = row.get("adx", 0)

    trend_ok = (not pd.isna(ema12) and not pd.isna(ema50)
                and not pd.isna(price) and price > 0
                and price > ema12 > ema50)
    signals["daily_trend"] = bool(trend_ok)
    # Strength berdasarkan ADX
    trend_strength = 50  # baseline
    if trend_ok:
        if not pd.isna(adx) and adx >= 25:
            trend_strength = 90
        elif not pd.isna(adx) and adx >= 20:
            trend_strength = 80
        else:
            trend_strength = 70
    else:
        # Partial: price > ema12 tapi ema12 < ema50
        if (not pd.isna(price) and not pd.isna(ema12)
                and price > ema12):
            trend_strength = 40  # short-term bullish, long-term masih bearish
        else:
            trend_strength = 15
    strengths["daily_trend"] = trend_strength
    total += trend_strength
    count += 1

    # ── 2. Weekly Trend Alignment ──
    weekly = row.get("weekly_trend", "NO_DATA")
    weekly_ok = weekly == "BULLISH"
    signals["weekly_trend"] = weekly_ok
    if weekly_ok:
        strengths["weekly_trend"] = 85
    elif weekly == "BEARISH":
        strengths["weekly_trend"] = 20
    else:
        strengths["weekly_trend"] = 40
    total += strengths["weekly_trend"]
    count += 1

    # ── 3. Volume Breakout ──
    vol_ratio = row.get("vol_ratio", 1.0)
    ret_20d = row.get("ret_20d", 0)
    vol_ok = (not pd.isna(vol_ratio) and vol_ratio > 1.2
              and not pd.isna(ret_20d) and ret_20d > 0)
    signals["volume"] = bool(vol_ok)
    if vol_ok:
        if vol_ratio > 2.0:
            strengths["volume"] = 90
        elif vol_ratio > 1.5:
            strengths["volume"] = 80
        else:
            strengths["volume"] = 65
    else:
        # Volume spike tapi harga turun = distribution
        if (not pd.isna(vol_ratio) and vol_ratio > 1.5
                and not pd.isna(ret_20d) and ret_20d < 0):
            strengths["volume"] = 10  # distribution
        else:
            strengths["volume"] = 35
    total += strengths["volume"]
    count += 1

    # ── 4. OBV Trend ──
    obv = row.get("obv_trend", 0)
    obv_ok = not pd.isna(obv) and obv > 0
    signals["obv"] = bool(obv_ok)
    strengths["obv"] = 75 if obv_ok else 30
    total += strengths["obv"]
    count += 1

    # ── 5. Donchian Breakout ──
    dc_breakout = row.get("dc_breakout", 0)
    dc_pos = row.get("dc_position", 50)
    dc_ok = (not pd.isna(dc_breakout) and dc_breakout > 0)
    signals["donchian"] = bool(dc_ok)
    if dc_ok:
        strengths["donchian"] = 85
    elif not pd.isna(dc_pos) and dc_pos > 75:
        strengths["donchian"] = 60  # near upper band
    elif not pd.isna(dc_pos) and dc_pos > 50:
        strengths["donchian"] = 45
    else:
        strengths["donchian"] = 20
    total += strengths["donchian"]
    count += 1

    # ── 6. Price vs EMA50 (momentum jangka menengah) ──
    pct_vs_ema50 = row.get("pct_vs_ema50", 0)
    ema50_ok = (not pd.isna(pct_vs_ema50) and pct_vs_ema50 > 0)
    signals["above_ema50"] = bool(ema50_ok)
    if ema50_ok:
        if pct_vs_ema50 > 5:
            strengths["above_ema50"] = 80  # strong momentum
        elif pct_vs_ema50 > 2:
            strengths["above_ema50"] = 70
        else:
            strengths["above_ema50"] = 55
    else:
        strengths["above_ema50"] = 20
    total += strengths["above_ema50"]
    count += 1

    # ── Final Confluence Score ──
    confluence = round(total / count, 1) if count > 0 else 50.0

    # ── Detail ──
    positive = sum(1 for v in signals.values() if v)
    detail = (
        f"Confluence: {confluence:.0f}/100 ({positive}/{count} signals positive)"
    )

    return {
        "confluence": confluence,
        "signal_count": positive,
        "total_signals": count,
        "signals": signals,
        "strengths": strengths,
        "detail": detail,
    }


def get_confluence_bonus(confluence_score: float) -> float:
    """
    Konversi confluence score ke bonus/malus untuk conviction.
    
    Confluence tinggi → bonus positif (tambah conviction)
    Confluence rendah → malus negatif (kurangi conviction)
    """
    if confluence_score >= 80:
        return 8.0   # strong confluence → tambah 8 poin
    elif confluence_score >= 65:
        return 4.0   # good confluence → tambah 4 poin
    elif confluence_score >= 50:
        return 0.0   # neutral
    elif confluence_score >= 35:
        return -4.0  # weak → kurangi 4 poin
    else:
        return -8.0  # very weak → kurangi 8 poin
