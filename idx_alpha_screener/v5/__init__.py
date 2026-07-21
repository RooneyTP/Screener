"""
v5 — 3-Profile Adaptive Scoring Engine
========================================
Bukan satu scoring generik, tapi 3 profil strategi yang aktif
tergantung kondisi saham & pasar.

Profil:
  📈 MOMENTUM PRO  — Saham tren naik, ADX kuat, breakout volume
  📉 REVERSAL PRO  — Saham oversold, fundamental ok, siap reversal
  💰 VALUE PRO     — Saham murah, dividen/buyback, arus asing masuk

Cara kerja:
  1. Deteksi profil yang cocok untuk tiap saham
  2. Skoring pake bobot spesifik per profil
  3. Threshold dinamis berdasarkan percentile (bukan fix)
  4. Bonus momentum of score (perubahan skor 5 hari)
"""

import logging
logger = logging.getLogger("v5")

enabled: bool = False
ab_test_mode: str = "v5_only"

# Default config — akan di-override dari config.yaml v5:
config: dict = {
    "score_momentum_weight": 1.3,
    "score_momentum_days": 5,
    "dynamic_percentile": True,
    "percentile_buy_target": 15,
    "percentile_sb_target": 5,
    "profile_switch_enabled": True,
}

# THRESHOLD DEFAULT (akan di-override config)
# Format per profil: [SB, BUY, WB, HOLD, SELL] — 5 level
THRESHOLDS = {
    "MOMENTUM": [68, 58, 50, 42, 35],
    "REVERSAL": [62, 52, 45, 38, 30],
    "VALUE":    [60, 52, 45, 38, 30],
}

# Bobot per komponen per profil
WEIGHTS = {
    "MOMENTUM": {
        "trend": 0.25, "volume": 0.20, "momentum_score": 0.18,
        "vwap": 0.12, "rsi": 0.08, "macd": 0.07,
        "weekly_trend": 0.10, "sr_proximity": 0.00,
        "foreign_flow": 0.00,
    },
    "REVERSAL": {
        "trend": 0.10, "volume": 0.15, "momentum_score": 0.10,
        "vwap": 0.15, "rsi": 0.20, "macd": 0.10,
        "weekly_trend": 0.05, "sr_proximity": 0.10,
        "foreign_flow": 0.05,
    },
    "VALUE": {
        "trend": 0.10, "volume": 0.10, "momentum_score": 0.08,
        "vwap": 0.10, "rsi": 0.08, "macd": 0.05,
        "weekly_trend": 0.05, "sr_proximity": 0.05,
        "foreign_flow": 0.39,
    },
}


def configure(cfg: dict):
    """Update dari config.yaml v5: section."""
    if not cfg:
        return
    global config, THRESHOLDS
    for key, val in cfg.items():
        if key == "thresholds" and isinstance(val, dict):
            for profile, th in val.items():
                if profile in THRESHOLDS:
                    THRESHOLDS[profile] = th
        elif key == "weights" and isinstance(val, dict):
            for profile, w in val.items():
                if profile in WEIGHTS:
                    WEIGHTS[profile].update(w)
        else:
            config[key] = val
    logger.info("v5 configured: %s", {k: v for k, v in config.items()})


def is_enabled() -> bool:
    return enabled


# ── Profil Detection ──

def detect_profile(row, regime, df_history=None) -> str:
    """
    Deteksi profil yang paling cocok untuk saham ini.
    
    MOMENTUM: ADX≥25, price>ema12>ema50, volume>avg
    REVERSAL: RSI<40, price<m tám 50% ema50, volume spike
    VALUE:    Fundamental murah (PE rendah), dividen yield tinggi

    Returns: 'MOMENTUM' | 'REVERSAL' | 'VALUE'
    """
    adx = row.get("adx", 0)
    rsi = row.get("rsi", 50)
    price = row.get("close", 0)
    ema12 = row.get("ema12", 0)
    ema50 = row.get("ema50", 0)
    vol_ratio = row.get("vol_ratio", 1.0)
    ret_20d = row.get("ret_20d", 0)
    pe = row.get("pe_ratio", None)
    pbv = row.get("pbv", None)
    div_yield = row.get("dividend_yield", None)

    # Cek MOMENTUM
    momentum_score = 0
    if not pd.isna(adx) and adx >= 22: momentum_score += 25
    if not pd.isna(price) and not pd.isna(ema12) and not pd.isna(ema50) and price > ema12 > ema50: momentum_score += 30
    if not pd.isna(vol_ratio) and vol_ratio > 1.3: momentum_score += 15
    if not pd.isna(ret_20d) and ret_20d > 0.02: momentum_score += 10

    # Cek REVERSAL
    reversal_score = 0
    if not pd.isna(rsi) and rsi < 42: reversal_score += 25
    if not pd.isna(price) and not pd.isna(ema50) and price < ema50: reversal_score += 15
    if not pd.isna(vol_ratio) and vol_ratio > 1.3 and (pd.isna(ret_20d) or ret_20d < 0): reversal_score += 15
    if not pd.isna(ret_20d) and ret_20d < -0.03: reversal_score += 10

    # Cek VALUE
    value_score = 0
    if pe is not None and not pd.isna(pe) and 5 <= pe <= 15: value_score += 25
    elif pe is not None and not pd.isna(pe) and 15 < pe <= 20: value_score += 15
    if pbv is not None and not pd.isna(pbv) and 0.5 <= pbv <= 2: value_score += 20
    if div_yield is not None and not pd.isna(div_yield) and div_yield > 0.03: value_score += 20

    # Profil dengan skor tertinggi
    scores = {"MOMENTUM": momentum_score, "REVERSAL": reversal_score, "VALUE": value_score}
    winner = max(scores, key=scores.get)

    # Threshold: minimal 30 poin untuk aktivasi profil
    if scores[winner] < 30:
        # Fallback: default MOMENTUM kalau ADX cukup, VALUE kalau PE ada
        if not pd.isna(adx) and adx >= 20:
            return "MOMENTUM"
        elif pe is not None and not pd.isna(pe) and 5 <= pe <= 25:
            return "VALUE"
        else:
            return "MOMENTUM"  # default
    
    return winner


# Need pandas for isnull checks
import pandas as pd
