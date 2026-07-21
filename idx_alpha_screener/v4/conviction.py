"""
v4/conviction.py — Dynamic Conviction Scoring Engine
=====================================================
Mengganti binary filter v3 (swing gate pass/fail, ADX < 15 → HOLD)
dengan conviction scoring berbasis 8 confluence factors.

Cara kerja:
  1. Hitung 8 factor scores (0-100 per factor)
  2. Bobot dinamis berdasarkan regime (BULL/BEAR/RANGING)
  3. Conviction = weighted average, lalu:
     - Soft penalty: ADX rendah → kurangi conviction (tidak langsung HOLD)
     - Soft penalty: IHSG bearish → kurangi conviction
     - Bonus: faktor dominan→ naikkan conviction
  4. Signal dari conviction level (lebih granular dari v3)

Perbedaan utama dari v3:
  - v3: score >= 64 AND swing gate pass AND ADX >= 15 → BUY, else HOLD
  - v4: conviction >= threshold → BUY (tapi conviction bisa ditopang
         faktor lain meski 1-2 faktor lemah)
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger("v4.conviction")

# ═══════════════════════════════════════════════════════════════
#  CONFIDENCE FACTOR WEIGHTS — Dynamic per Regime
# ═══════════════════════════════════════════════════════════════
# Bobot ini menentukan seberapa besar pengaruh tiap faktor
# terhadap conviction score akhir.
FACTOR_WEIGHTS = {
    "BULL": {
        "trend":        0.18,   # Trend alignment + ADX
        "volume":       0.14,   # Volume confirmation
        "relative_strength": 0.14,  # vs IHSG
        "vwap":         0.12,   # VWAP proximity
        "rsi":          0.10,   # RSI momentum
        "macd":         0.08,   # MACD
        "weekly_trend": 0.14,   # Weekly alignment — NEW
        "sr_proximity": 0.10,   # Support/Resistance — NEW
    },
    "BEAR": {
        "trend":        0.15,
        "volume":       0.15,
        "relative_strength": 0.18,  # Di BEAR, relative strength paling penting
        "vwap":         0.12,
        "rsi":          0.08,
        "macd":         0.07,
        "weekly_trend": 0.12,
        "sr_proximity": 0.13,   # Support resist jadi penting di BEAR
    },
    "RANGING": {
        "trend":        0.16,
        "volume":       0.14,
        "relative_strength": 0.14,
        "vwap":         0.14,
        "rsi":          0.10,
        "macd":         0.08,
        "weekly_trend": 0.12,
        "sr_proximity": 0.12,
    },
    "HIGH_VOLATILITY": {
        "trend":        0.15,
        "volume":       0.14,
        "relative_strength": 0.12,
        "vwap":         0.12,
        "rsi":          0.10,
        "macd":         0.08,
        "weekly_trend": 0.12,
        "sr_proximity": 0.17,   # S/R penting di high vol untuk entry
    },
}

# ═══════════════════════════════════════════════════════════════
#  CONVICTION THRESHOLDS — v4 lebih granular
# ═══════════════════════════════════════════════════════════════
# v3: [72, 62, 52, 38] untuk BULL
# v4: [78, 68, 58, 48, 38] — ada EXTRA_CAUTIOUS di antaranya
THRESHOLDS = {
    "BULL":            [78, 68, 58, 48, 38],
    "BEAR":            [85, 75, 65, 55, 45],
    "RANGING":         [80, 70, 60, 50, 40],
    "HIGH_VOLATILITY": [80, 70, 60, 50, 40],
}

# Default config (bisa di-override via config.yaml v4:)
DEFAULT_CONFIG = {
    "adx_no_trend_penalty": 0.08,    # Kurangi 8% conviction per poin ADX < 15
    "ihsg_bear_penalty": 5,          # Kurangi 5 poin conviction jika IHSG bearish
    "weekly_bonus": 3,               # Tambah 3 poin jika weekly trend align
    "conviction_bonus_factor_count": 5,  # Bonus jika ≥5 faktor positif
    "conviction_penalty_factor_count": 2, # Penalty jika ≤2 faktor positif
}


def _clamp(val, lo=0, hi=100):
    return max(lo, min(hi, val))


# ═══════════════════════════════════════════════════════════════
#  1. TREND FACTOR — EMA alignment + ADX (reuse dari scoring.py logic)
# ═══════════════════════════════════════════════════════════════
def _factor_trend(row: pd.Series) -> float:
    """Trend factor 0-100. ADX rendah = penalty gradual, bukan langsung HOLD."""
    ema12 = row.get("ema12", 0)
    ema50 = row.get("ema50", 0)
    price = row.get("close", 0)
    adx = row.get("adx", 0)

    if pd.isna(ema12) or pd.isna(ema50) or price == 0:
        return 30

    score = 30  # baseline

    # EMA Alignment
    if price > ema12 > ema50:
        score += 30  # full bullish
    elif price > ema12 and ema12 < ema50:
        score += 12  # short-term bullish
    elif price < ema12 and ema12 > ema50:
        score += 5   # pullback in uptrend
    elif price < ema12 < ema50:
        score -= 10  # full bearish

    # ADX — gradual, bukan cutoff
    if not pd.isna(adx):
        if adx >= 30:
            score += 15  # strong trend
        elif adx >= 25:
            score += 10
        elif adx >= 20:
            score += 5
        elif adx >= 15:
            score += 2   # developing
        # ADX < 15: tidak dapat bonus, tapi tidak kena cutoff -20
        # (penalty terpisah di conviction scoring)
    
    # DI Direction
    plus_di = row.get("plus_di", 0)
    minus_di = row.get("minus_di", 0)
    if not pd.isna(plus_di) and not pd.isna(minus_di):
        if plus_di > minus_di:
            score += 5
        else:
            score -= 5

    return _clamp(score, 0, 100)


# ═══════════════════════════════════════════════════════════════
#  2. VOLUME FACTOR
# ═══════════════════════════════════════════════════════════════
def _factor_volume(row: pd.Series) -> float:
    """Volume factor 0-100. Lebih granular dari v3 — ada reward buat moderate volume."""
    vol_ratio = row.get("vol_ratio", 1.0)
    ret_20d = row.get("ret_20d", 0)
    avg_vol = row.get("avg_vol_60d", 0)

    if pd.isna(vol_ratio) or vol_ratio == 0:
        return 40

    # Liquidity penalty (gradual)
    if not pd.isna(avg_vol) and avg_vol > 0:
        if avg_vol < 500_000:
            return 25
        elif avg_vol < 1_000_000:
            return 35

    # Volume + price
    if not pd.isna(ret_20d):
        if vol_ratio > 1.5 and ret_20d > 5:
            return 85  # strong breakout
        elif vol_ratio > 1.5 and ret_20d > 0:
            return 75  # healthy volume + price up
        elif vol_ratio > 1.2 and ret_20d > 0:
            return 65  # moderate
        elif vol_ratio > 1.0 and ret_20d > 0:
            return 55  # slight
        elif vol_ratio > 0.8:
            return 45  # neutral-low
        elif vol_ratio > 1.5 and ret_20d < -5:
            return 15  # distribution
        elif vol_ratio > 1.0 and ret_20d < -3:
            return 30  # selling pressure
        else:
            return 40

    return 45


# ═══════════════════════════════════════════════════════════════
#  3. RELATIVE STRENGTH FACTOR (vs IHSG)
# ═══════════════════════════════════════════════════════════════
def _factor_relative_strength(row: pd.Series) -> float:
    """Relative strength vs IHSG — 0-100."""
    ret_20d = row.get("ret_20d", 0)
    idx_ret = row.get("idx_ret_20d", 0)

    if pd.isna(ret_20d):
        return 40
    if pd.isna(idx_ret) or idx_ret == 0:
        # Fallback
        if ret_20d > 5: return 60
        elif ret_20d > 0: return 50
        else: return 30

    relative = ret_20d - idx_ret  # outperformance
    if relative > 8:  return 90
    elif relative > 5: return 80
    elif relative > 3: return 70
    elif relative > 0: return 60
    elif relative > -3: return 45
    elif relative > -8: return 30
    else: return 15


# ═══════════════════════════════════════════════════════════════
#  4. VWAP FACTOR
# ═══════════════════════════════════════════════════════════════
def _factor_vwap(row: pd.Series) -> float:
    """VWAP proximity — 0-100. Lebih granular, reward mean reversion."""
    pct = row.get("pct_vs_vwap", 0)
    if pd.isna(pct):
        return 40

    # Above VWAP (bullish bias — gradual)
    if 0 < pct <= 1.5: return 78   # sweet spot
    elif 1.5 < pct <= 3: return 68 # still good
    elif 3 < pct <= 5: return 50   # extended
    elif 5 < pct <= 8: return 35   # overextended
    elif pct > 8: return 20        # too high

    # Below VWAP
    elif -1.5 <= pct < 0: return 50  # slight dip, mean reversion opportunity
    elif -3 <= pct < -1.5: return 40
    elif -5 <= pct < -3: return 30
    elif pct < -5: return 15

    return 40


# ═══════════════════════════════════════════════════════════════
#  5. RSI FACTOR
# ═══════════════════════════════════════════════════════════════
def _factor_rsi(row: pd.Series) -> float:
    """RSI momentum — 0-100."""
    rsi = row.get("rsi", 50)
    stoch_k = row.get("stoch_k", 50)
    stoch_d = row.get("stoch_d", 50)

    if pd.isna(rsi) or rsi == 0:
        return 30

    rising = (not pd.isna(stoch_k) and not pd.isna(stoch_d)
              and stoch_k > stoch_d)

    if rsi < 30: return 15       # falling knife
    elif rsi < 35: return 25 if rising else 18
    elif rsi < 40: return 50 if rising else 35
    elif rsi < 50: return 70 if rising else 50   # sweet spot
    elif rsi < 55: return 65 if rising else 50
    elif rsi < 60: return 55 if rising else 40
    elif rsi < 65: return 35 if rising else 25
    elif rsi < 70: return 25
    else: return 15


# ═══════════════════════════════════════════════════════════════
#  6. MACD FACTOR
# ═══════════════════════════════════════════════════════════════
def _factor_macd(row: pd.Series) -> float:
    """MACD confirmation — 0-100."""
    hist = row.get("macd_hist", 0)
    signal = row.get("macd_signal", 0)
    macd = row.get("macd", 0)
    ema12 = row.get("ema12", 0)
    price = row.get("close", 0)

    if pd.isna(hist) or pd.isna(signal):
        return 40

    macd_bullish = macd > signal
    ema_confirm = (not pd.isna(ema12) and not pd.isna(price)
                   and price > ema12)

    if macd_bullish and hist > 0 and ema_confirm: return 75
    elif macd_bullish and hist > 0: return 60
    elif macd_bullish: return 50
    elif not macd_bullish and hist < 0 and not ema_confirm: return 25
    elif not macd_bullish and hist > 0: return 40
    else: return 45


# ═══════════════════════════════════════════════════════════════
#  7. WEEKLY TREND ALIGNMENT — NEW di v4
# ═══════════════════════════════════════════════════════════════
def _factor_weekly_trend(row: pd.Series) -> float:
    """
    Weekly trend alignment — 0-100.
    Menggunakan weekly_trend dari swing_filters yang sudah dihitung.

    Logic:
    - Jika weekly trend BULLISH dan daily juga bullish → high score
    - Jika weekly BEARISH tapi daily bullish → mean reversion (medium-low)
    - Jika weekly BEARISH dan daily bearish → low score
    """
    weekly = row.get("weekly_trend", "NO_DATA")
    ema12 = row.get("ema12", 0)
    ema50 = row.get("ema50", 0)
    price = row.get("close", 0)

    daily_bullish = (not pd.isna(ema12) and not pd.isna(ema50)
                     and price > ema12 > ema50)
    daily_neutral = (not pd.isna(ema12) and price > ema12)

    if weekly == "BULLISH" and daily_bullish:
        return 85  # multi-timeframe bullish alignment
    elif weekly == "BULLISH" and daily_neutral:
        return 65  # weekly bullish, daily recovering
    elif weekly == "BULLISH":
        return 55  # weekly bullish, daily bearish (lagging)
    elif weekly == "BEARISH" and daily_bullish:
        return 45  # diverging — daily bullish vs weekly bearish
    elif weekly == "BEARISH" and daily_neutral:
        return 35
    elif weekly == "BEARISH":
        return 20  # multi-timeframe bearish
    else:
        return 40  # NO_DATA


# ═══════════════════════════════════════════════════════════════
#  8. SUPPORT/RESISTANCE PROXIMITY — NEW di v4
# ═══════════════════════════════════════════════════════════════
def _factor_sr_proximity(row: pd.Series) -> float:
    """
    Support/Resistance proximity — 0-100.

    Logic:
    - Harga dekat support + jauh dari resistance → bagus (upside > downside)
    - Harga di tengah → netral
    - Harga dekat resistance → kurang bagus
    """
    price = row.get("close", 0)
    support = row.get("nearest_support", 0)
    resistance = row.get("nearest_resistance", 0)

    if price == 0:
        return 40

    # Fallback: pakai ATR jika S/R tidak tersedia
    if support == 0 or resistance == 0 or support >= resistance:
        atr = row.get("atr", 0)
        if atr > 0 and price > 0:
            atr_pct = (atr / price) * 100
            if 1.0 <= atr_pct <= 3.0:
                return 60  # volatilitas wajar
            elif atr_pct < 1.0:
                return 35  # terlalu sepi
            else:
                return 40  # terlalu volatile
        return 45

    # Distance ke support dan resistance (%)
    dist_to_support = (price - support) / price * 100
    dist_to_resistance = (resistance - price) / price * 100

    # Risk/Reward dari S/R
    if dist_to_support < 2.0 and dist_to_resistance > dist_to_support * 2:
        return 80  # dekat support, jauh resistance = bagus
    elif dist_to_support < 3.0 and dist_to_resistance > dist_to_support * 1.5:
        return 70  # cukup bagus
    elif dist_to_support < 5.0 and dist_to_resistance > dist_to_support:
        return 60  # lumayan
    elif dist_to_support < 2.0 and dist_to_resistance < dist_to_support * 1.5:
        return 45  # dekat support TAPI juga dekat resistance = range bound
    elif dist_to_resistance < 3.0:
        return 30  # dekat resistance, upside terbatas
    elif dist_to_support > 10:
        return 25  # jauh dari support, downside besar
    else:
        return 40


# ═══════════════════════════════════════════════════════════════
#  MASTER COMPUTE — Conviction Score
# ═══════════════════════════════════════════════════════════════
# Mapping factor → function
_FACTOR_FUNCS = {
    "trend": _factor_trend,
    "volume": _factor_volume,
    "relative_strength": _factor_relative_strength,
    "vwap": _factor_vwap,
    "rsi": _factor_rsi,
    "macd": _factor_macd,
    "weekly_trend": _factor_weekly_trend,
    "sr_proximity": _factor_sr_proximity,
}


def compute_conviction(row: pd.Series, regime: str = "RANGING",
                       config: Optional[dict] = None) -> dict:
    """
    Hitung conviction score untuk satu baris data.

    Parameters
    ----------
    row : pd.Series
        Baris terakhir dari DataFrame (sudah compute_all_indicators)
    regime : str
        Regime pasar (BULL/BEAR/RANGING/HIGH_VOLATILITY)
    config : dict, optional
        Override config (dari v4 section config.yaml)

    Returns
    -------
    dict dengan keys:
        conviction : float (0-100)
        signal : str (STRONG_BUY/BUY/WEAK_BUY/HOLD/SELL)
        factors : dict of {factor_name: score}
        weights : dict of {factor_name: weight}
        breakdown : str (penjelasan singkat)
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    weights = FACTOR_WEIGHTS.get(regime, FACTOR_WEIGHTS["RANGING"])
    thresholds = THRESHOLDS.get(regime, THRESHOLDS["RANGING"])

    # 1. Compute all factor scores
    factors = {}
    for name, func in _FACTOR_FUNCS.items():
        try:
            factors[name] = func(row)
        except Exception as e:
            logger.debug("Factor %s error: %s", name, e)
            factors[name] = 45  # neutral fallback

    # 2. Weighted conviction
    conviction = sum(
        factors[name] * weights.get(name, 0.1)
        for name in factors
    )

    # 3. Soft penalties — ADX rendah
    adx = row.get("adx", 0)
    if not pd.isna(adx) and adx < 15:
        penalty = (15 - adx) * cfg["adx_no_trend_penalty"] * 10
        conviction -= penalty
        logger.debug("ADX penalty: %.1f (ADX=%.1f, penalty=%.1f)", penalty, adx, penalty)

    # 4. Soft penalty — IHSG bearish
    idx_ret = row.get("idx_ret_20d", 0)
    if not pd.isna(idx_ret) and idx_ret < -3:
        conviction -= cfg["ihsg_bear_penalty"]

    # 5. Conviction bonus — banyak faktor positif
    positive_factors = sum(1 for v in factors.values() if v >= 60)
    if positive_factors >= cfg["conviction_bonus_factor_count"]:
        conviction += cfg.get("weekly_bonus", 3)  # reuse weekly_bonus sebagai "conviction bonus"
    elif positive_factors <= cfg["conviction_penalty_factor_count"]:
        conviction -= 5

    # 6. Clamp
    conviction = round(_clamp(conviction, 0, 100), 1)

    # 7. Classify
    sb, b, wb, h, _ = thresholds  # [STRONG_BUY, BUY, WEAK_BUY, HOLD, SELL_min]
    if conviction >= sb:
        signal = "STRONG_BUY"
    elif conviction >= b:
        signal = "BUY"
    elif conviction >= wb:
        signal = "WEAK_BUY"
    elif conviction >= h:
        signal = "HOLD"
    else:
        signal = "SELL"

    # 8. Breakdown
    factor_lines = [f"  {k}: {v:.0f}" for k, v in
                    sorted(factors.items(), key=lambda x: x[1], reverse=True)]
    breakdown = (
        f"Conviction {conviction:.1f} → {signal} (regime={regime})\n"
        f"  Positif factors: {positive_factors}/8\n"
        + "\n".join(factor_lines[:5])  # top 5 faktor
    )

    return {
        "conviction": conviction,
        "signal": signal,
        "factors": factors,
        "weights": weights,
        "positive_factors": positive_factors,
        "breakdown": breakdown,
    }
