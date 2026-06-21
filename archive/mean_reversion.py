"""
mean_reversion.py — Mean Reversion Strategy Module
Deteksi kondisi oversold/overbought untuk bounce trading.
Jalan paralel dengan trend following screener.
"""

import pandas as pd
import numpy as np
import logging
logger = logging.getLogger(__name__)

def detect_mean_reversion(close, high, low, volume, rsi_series, bb_low, bb_up, bb_mid, atr_series, volume_sma) -> dict:
    """
    Deteksi setup mean reversion.
    
    Returns dict:
        signal: "BUY_BOUNCE" / "SELL_FADE" / "NONE"
        confidence: 0-100
        entry, tp, sl: float
    """
    result = {
        "signal": "NONE",
        "confidence": 0,
        "entry": 0.0,
        "tp": 0.0,
        "sl": 0.0,
        "reason": []
    }
    
    if len(close) < 20:
        logger.debug("[MR] Data too short")
        return result
    
    price = float(close.iloc[-1])
    rsi = float(rsi_series.iloc[-1])
    bb_l = float(bb_low.iloc[-1])
    bb_u = float(bb_up.iloc[-1])
    bb_m = float(bb_mid.iloc[-1])
    atr = float(atr_series.iloc[-1])
    vol = float(volume.iloc[-1])
    vol_sma = float(volume_sma.iloc[-1]) if len(volume_sma) > 0 else vol
    
    score = 0
    
    # ── BUY BOUNCE (Oversold) ──
    if rsi < 35:
        score += 20
        result["reason"].append("RSI_Oversold")
    if price <= bb_l * 1.02:
        score += 25
        result["reason"].append("Near_BB_Low")
    if vol > vol_sma * 1.3:
        score += 20
        result["reason"].append("Volume_Spike")
    
    # Cek bullish divergence
    if len(close) >= 10 and len(rsi_series) >= 10:
        price_min_recent = float(close.tail(5).min())
        price_min_prev = float(close.iloc[-10:-5].min())
        rsi_min_recent = float(rsi_series.tail(5).min())
        rsi_min_prev = float(rsi_series.iloc[-10:-5].min())
        if price_min_recent < price_min_prev and rsi_min_recent > rsi_min_prev:
            score += 25
            result["reason"].append("Bullish_Div")
    
    if score >= 45:
        result["signal"] = "BUY_BOUNCE"
        result["confidence"] = min(100, score)
        result["entry"] = price
        result["tp"] = round(bb_m, 0)  # Target: middle BB
        result["sl"] = round(price - (1.5 * atr), 0)
        return result
    
    # ── SELL FADE (Overbought) ──
    score = 0
    if rsi > 70:
        score += 20
        result["reason"].append("RSI_Overbought")
    if price >= bb_u * 0.98:
        score += 25
        result["reason"].append("Near_BB_High")
    if vol < vol_sma * 0.7:
        score += 15
        result["reason"].append("Volume_DryUp")
    
    # Bearish divergence
    if len(close) >= 10 and len(rsi_series) >= 10:
        price_max_recent = float(close.tail(5).max())
        price_max_prev = float(close.iloc[-10:-5].max())
        rsi_max_recent = float(rsi_series.tail(5).max())
        rsi_max_prev = float(rsi_series.iloc[-10:-5].max())
        if price_max_recent > price_max_prev and rsi_max_recent < rsi_max_prev:
            score += 25
            result["reason"].append("Bearish_Div")
    
    if score >= 45:
        result["signal"] = "SELL_FADE"
        result["confidence"] = min(100, score)
        result["entry"] = price
        result["tp"] = round(bb_m, 0)
        result["sl"] = round(price + (1.5 * atr), 0)
        return result
    
    return result


def should_use_mean_reversion(ihsg_regime: str, market_breadth_pct: float) -> bool:
    """
    Tentukan apakah market cocok untuk mean reversion.
    Aktif saat RANGING atau mixed market (bukan trending kuat).
    """
    if ihsg_regime == "RANGING":
        return True
    if 30 <= market_breadth_pct <= 60:
        return True  # Mixed market = bagus untuk mean reversion
    if ihsg_regime in ("HIGH_VOLATILITY",) and market_breadth_pct < 20:
        return True  # Panic selling = bounce opportunity
    return False
