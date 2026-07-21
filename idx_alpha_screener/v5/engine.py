"""
v5/engine.py — V5 Master Engine
==================================
Menggabungkan: profil detection → conviction per profil → momentum score → threshold dinamis.

Flow:
  1. Record score ke momentum_score + dynamic_threshold
  2. Deteksi profil (MOMENTUM/REVERSAL/VALUE)
  3. Hitung conviction pake bobot profil
  4. Apply momentum of score (bonus/penalty dari perubahan skor)
  5. Classify pake threshold dinamis (percentile-based)
  6. Return signal + metadata
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional

from v5 import (
    THRESHOLDS, WEIGHTS, config, detect_profile
)
from v5 import momentum_score as ms
from v5 import dynamic_threshold as dt

logger = logging.getLogger("v5.engine")


def process_stock(ticker: str, row: pd.Series, regime: str,
                  df_history: Optional[pd.DataFrame] = None) -> dict:
    """
    Proses satu saham dengan v5 engine.

    Parameters
    ----------
    ticker : str (tanpa .JK)
    row : pd.Series — baris terakhir dari dataframe
    regime : str — BULL/BEAR/RANGING/HIGH_VOLATILITY
    df_history : optional — dataframe lengkap untuk ambil trend

    Returns
    -------
    dict dengan keys:
        score : float — final score
        signal : str
        profile : str — profil yang dipilih
        profile_score : dict — skor tiap profil
        momentum_delta : float — perubahan skor 5 hari
        thresholds : list — threshold yang dipakai
    """
    # ── 1. Dapatkan conviction dasar dari v4 engine ──
    # (reuse fungsi scoring dari v4 daripada rebuild dari nol)
    from v4.conviction import compute_conviction
    from v4.confluence import score_confluence, get_confluence_bonus

    conv = compute_conviction(row, regime, {})
    conf = score_confluence(row)
    conf_bonus = get_confluence_bonus(conf["confluence"])
    base_score = round(max(0, min(100, conv["conviction"] + conf_bonus * 0.5)), 1)

    # ── 2. Record ke momentum tracker ──
    ms.record(ticker, base_score)
    dt.record(base_score)

    # ── 3. Deteksi profil ──
    profile = detect_profile(row, regime, df_history)

    # ── 4. Skor spesifik profil ──
    weights = WEIGHTS.get(profile, WEIGHTS["MOMENTUM"])

    # Hitung ulang pake bobot profil
    trend_s = conv.get("factors", {}).get("trend", 50)
    vol_s = conv.get("factors", {}).get("volume", 50)
    rel_s = conv.get("factors", {}).get("relative_strength", 50)
    vwap_s = conv.get("factors", {}).get("vwap", 50)
    rsi_s = conv.get("factors", {}).get("rsi", 50)
    macd_s = conv.get("factors", {}).get("macd", 50)
    weekly_s = conv.get("factors", {}).get("weekly_trend", 50)
    sr_s = conv.get("factors", {}).get("sr_proximity", 50)

    profile_score = (
        trend_s * weights.get("trend", 0.15) +
        vol_s * weights.get("volume", 0.15) +
        (rel_s if "relative_strength" not in weights else
         conv.get("factors", {}).get("relative_strength", 50)) * weights.get("relative_strength", 0.10) +
        vwap_s * weights.get("vwap", 0.12) +
        rsi_s * weights.get("rsi", 0.10) +
        macd_s * weights.get("macd", 0.07) +
        weekly_s * weights.get("weekly_trend", 0.08) +
        sr_s * weights.get("sr_proximity", 0.05) +
        # foreign_flow — 0 untuk sekarang (belum ada data)
        50 * weights.get("foreign_flow", 0.00) +
        # momentum_score bonus
        50 * weights.get("momentum_score", 0.0)
    )
    profile_score = round(max(0, min(100, profile_score)), 1)

    # ── 5. Apply momentum of score ──
    mom_weight = config.get("score_momentum_weight", 1.3)
    mom_days = config.get("score_momentum_days", 5)
    final_score = ms.compute_momentum(ticker, profile_score, mom_days, mom_weight)

    # ── 6. Threshold dinamis ──
    use_percentile = config.get("dynamic_percentile", True)
    if use_percentile:
        p_sb = config.get("percentile_sb_target", 5)
        p_b = config.get("percentile_buy_target", 15)
        p_wb = config.get("percentile_wb_target", 30)
        p_h = config.get("percentile_hold_target", 50)
        base_th = THRESHOLDS.get(profile, [68, 58, 50, 42, 35])
        thresholds = dt.get_thresholds(profile, p_sb, p_b, p_wb, p_h, base_th)
    else:
        thresholds = THRESHOLDS.get(profile, [68, 58, 50, 42, 35])

    # ── 7. Classify ──
    sb, b, wb, h, _ = thresholds
    if final_score >= sb:       signal = "STRONG_BUY"
    elif final_score >= b:      signal = "BUY"
    elif final_score >= wb:     signal = "WEAK_BUY"
    elif final_score >= h:      signal = "HOLD"
    else:                       signal = "SELL"

    # ── 8. Metadata ──
    delta = ms.get_delta(ticker, mom_days)

    return {
        "score": final_score,
        "signal": signal,
        "profile": profile,
        "base_score": base_score,
        "profile_score": profile_score,
        "momentum_delta": delta,
        "thresholds": thresholds,
        "conf_bonus": conf_bonus,
    }
