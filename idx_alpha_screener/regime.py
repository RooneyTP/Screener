"""
regime.py — Market Regime Detection for IDX Alpha Screener v2
==============================================================
"""

import logging
import numpy as np
import pandas as pd
from typing import Tuple

logger = logging.getLogger("regime")


def detect_market_regime(df: pd.DataFrame) -> Tuple[str, float, float]:
    """
    Deteksi regime pasar berdasarkan EMA crossover + ADX.
    
    Returns:
        (regime, market_trend_score, current_adx)
        regime: "BULL", "BEAR", "HIGH_VOLATILITY", "RANGING"
        market_trend_score: positif = bullish, negatif = bearish
        current_adx: nilai ADX untuk konfirmasi
    """
    if df.empty or len(df) < 50:
        return "RANGING", 0.0, 0.0

    close = df["close"]

    # EMA crossover
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    ema12_now = ema12.iloc[-1]
    ema50_now = ema50.iloc[-1]

    # Price relative to EMA50
    price = close.iloc[-1]
    pct_vs_ema50 = (price - ema50_now) / ema50_now * 100

    # ADX
    adx_val = df["adx"].iloc[-1] if "adx" in df.columns else 0
    if pd.isna(adx_val):
        adx_val = 0

    # Determine trend direction and strength
    ema_diff_pct = (ema12_now - ema50_now) / ema50_now * 100
    
    # Market trend score: -100 to +100
    trend_score = ema_diff_pct * 2  # Scale
    # Blend EMA alignment with price position (sign otomatis: positif=bullish, negatif=bearish)
    trend_score = trend_score * 0.5 + pct_vs_ema50 * 0.5

    trend_score = max(-100, min(100, trend_score))

    # Regime classification
    if adx_val > 30 and ema_diff_pct > 1.0 and price > ema50_now:
        regime = "BULL"
    elif adx_val > 30 and ema_diff_pct < -1.0 and price < ema50_now:
        regime = "BEAR"
    elif adx_val > 30:
        regime = "HIGH_VOLATILITY"
    else:
        regime = "RANGING"

    return regime, round(trend_score, 1), round(float(adx_val), 1)
