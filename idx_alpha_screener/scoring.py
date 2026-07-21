"""
scoring.py — Multi-Factor Scoring Engine v3
=============================================
BACKTEST-PROVEN REWRITE (Juli 2026):
- Scoring 60-62: SATU-SATUNYA grup dengan return positif (+0.07% avg)
- Scoring > 62: INVERTED — makin tinggi makin buruk performa
- Scoring < 58: noise, WR di bawah tebak acak

SOLUSI:
1. Turunkan ceiling RSI/VWAP/Volume — komponen yg bikin score inflated = false signal
2. Tambah IHSG-relative strength — komponen paling underrated
3. Reward "moderate setup" — punish "everything perfect" (itu trap)
4. ADX > 20 sebagai prerequisite (tanpa trend, jangan trading)
"""

import os
import glob
import statistics
from datetime import datetime, date
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger("scoring")

# ── Bobot v3 — BACKTEST-PROVEN ────────────────────────────────────
# Insight kunci dari 21.411 sinyal historis:
#   - RSI oversold (falling knife) = negative returns
#   - RSI > 65 (overbought) = sering reversal
#   - VWAP premium > 3% = extended, risiko pullback
#   - Volume + harga confirmation = OK tapi jangan terlalu di-reward
#   - IHSG relative strength = GAME CHANGER (yang paling kurang)
#   - Trend alignment (EMA) = harus ada, bukan bonus
WEIGHTS = {
    "BULL": {
        "trend":     0.25,   # EMA alignment + ADX
        "volume":    0.18,   # Volume confirmation
        "ihsg_rel":  0.18,   # IHSG relative strength ← NEW
        "vwap":      0.14,   # VWAP proximity
        "rsi":       0.10,   # RSI momentum (reduced)
        "macd":      0.08,   # MACD confirmation (reduced)
        "stoch":     0.07,   # Stochastic (reduced)
    },
    "BEAR": {
        "trend":     0.20,
        "volume":    0.20,
        "ihsg_rel":  0.20,   # Di BEAR, IHSG relatif = paling penting
        "vwap":      0.15,
        "rsi":       0.08,
        "macd":      0.07,
        "stoch":     0.10,
    },
    "RANGING": {
        "trend":     0.22,
        "volume":    0.18,
        "ihsg_rel":  0.15,
        "vwap":      0.18,
        "rsi":       0.12,
        "macd":      0.08,
        "stoch":     0.07,
    },
    "HIGH_VOLATILITY": {
        "trend":     0.20,
        "volume":    0.18,
        "ihsg_rel":  0.15,
        "vwap":      0.15,
        "rsi":       0.10,
        "macd":      0.08,
        "stoch":     0.14,   # Stochastic lebih berguna di high vol
    },
}


def _norm(val, lo=0, hi=100):
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, val))


# ════════════════════════════════════════════════════════════════
#  1. TREND — Komponen paling penting (bobot terbesar)
# ════════════════════════════════════════════════════════════════
def score_trend(row: pd.Series) -> float:
    """
    Trend scoring v3: ADX sebagai gate, EMA sebagai konfirmasi.
    
    Logic:
    - ADX < 20: weak/no trend → maksimal score 50 (tidak bisa jadi BUY)
    - ADX ≥ 20 + price > EMA12 > EMA50: bullish alignment → high score
    - ADX ≥ 25 + strong alignment: bonus
    - ADX < 15: penalty besar (no trend = no trade)
    """
    ema12 = row.get("ema12", 0)
    ema50 = row.get("ema50", 0)
    price = row.get("close", 0)
    adx = row.get("adx", 0)
    plus_di = row.get("plus_di", 0)
    minus_di = row.get("minus_di", 0)

    if pd.isna(ema12) or pd.isna(ema50) or price == 0:
        return 30

    # ── ADX Gate ──
    if pd.isna(adx) or adx < 15:
        return 20   # No trend = no trade
    
    score = 30  # baseline

    # ── EMA Alignment ──
    if price > ema12 > ema50:
        score += 30  # full bullish alignment
    elif price > ema12 and ema12 < ema50:
        score += 12  # short-term bullish, LT masih bearish
    elif price < ema12 and ema12 > ema50:
        score += 5   # short-term pullback di uptrend
    elif price < ema12 < ema50:
        score -= 10  # full bearish alignment

    # ── ADX Strength Bonus ──
    if adx >= 30:
        score += 12  # strong trend
    elif adx >= 25:
        score += 8   # moderately strong
    elif adx >= 20:
        score += 3   # developing trend
    
    # ── DI Direction ──
    if not pd.isna(plus_di) and not pd.isna(minus_di):
        if plus_di > minus_di:
            score += 5   # bullish direction
        else:
            score -= 5   # bearish direction

    return _norm(score, 0, 100)


# ════════════════════════════════════════════════════════════════
#  2. VOLUME — Konfirmasi, bukan trigger
# ════════════════════════════════════════════════════════════════
def score_volume(row: pd.Series) -> float:
    """
    Volume scoring v3: LEBIH KONSERVATIF.
    
    Insight backtest: volume spike > 1.5x dengan harga naik pun tidak
    menjamin (false breakout). Reward moderate, jangan over-reward.
    
    Cap maksimal 80 (volume saja tidak cukup untuk score tinggi).
    """
    vol_ratio = row.get("vol_ratio", 1.0)
    ret_20d = row.get("ret_20d", 0)
    avg_vol_60d = row.get("avg_vol_60d", 0)
    last_vol = row.get("volume", 0)

    if pd.isna(vol_ratio) or vol_ratio == 0:
        return 40

    # ── Liquidity check — jika volume harian terlalu kecil ──
    if not pd.isna(avg_vol_60d) and avg_vol_60d > 0:
        if avg_vol_60d < 1_000_000:
            return 25  # volume terlalu kecil untuk trading
    
    # ── Volume + Price confirmation ──
    if not pd.isna(ret_20d):
        if vol_ratio > 1.5 and ret_20d > 0:
            return 75  # volume surge + harga naik (tapi cap 75)
        elif vol_ratio > 1.5 and ret_20d > 3:
            return 80  # kuat, masih ada upside
        elif vol_ratio > 1.2 and ret_20d > 0:
            return 65  # moderate
        elif vol_ratio > 1.0 and ret_20d > 0:
            return 55  # slight
        elif vol_ratio > 1.5 and ret_20d < -3:
            return 15  # distribution — heavy selling
        elif vol_ratio > 1.0 and ret_20d < -3:
            return 30  # selling pressure
        elif vol_ratio > 1.0 and ret_20d < 0:
            return 40  # mild selling
        else:
            return 50  # neutral
    return 50


# ════════════════════════════════════════════════════════════════
#  3. IHSG RELATIVE STRENGTH — KOMPONEN BARU
# ════════════════════════════════════════════════════════════════
def score_ihsg_relative(row: pd.Series) -> float:
    """
    IHSG Relative Strength: seberapa kuat saham vs IHSG.
    
    Logic:
    - Bandingkan ret_20d saham vs idx_ret_20d
    - Jika saham outperform IHSG → bagus (ada kekuatan relatif)
    - Jika saham underperform IHSG → waspada
    
    Backtest insight: saham yang outperform IHSG di 20 hari terakhir
    biasanya terus outperform dalam 5 hari ke depan (momentum).
    """
    ret_20d = row.get("ret_20d", 0)
    idx_ret_20d = row.get("idx_ret_20d", 0)
    
    if pd.isna(ret_20d):
        return 40
    
    if pd.isna(idx_ret_20d) or idx_ret_20d == 0:
        # Fallback: pakai absolute return sebagai proxy
        if ret_20d > 5:
            return 60
        elif ret_20d > 0:
            return 50
        else:
            return 30
    
    # Relative strength = stock return - market return
    relative = ret_20d - idx_ret_20d
    
    if relative > 8:
        return 90  # strongly outperforming
    elif relative > 5:
        return 80
    elif relative > 3:
        return 70
    elif relative > 0:
        return 60  # outperforming
    elif relative > -3:
        return 45  # slightly underperforming
    elif relative > -8:
        return 30  # underperforming
    else:
        return 15  # severely underperforming


# ════════════════════════════════════════════════════════════════
#  4. VWAP — Entry zone quality (tidak over-reward)
# ════════════════════════════════════════════════════════════════
def score_vwap(row: pd.Series) -> float:
    """
    VWAP scoring v3: LEBIH KONSERVATIF.
    
    Backtest insight: price 0-3% above VWAP = sweet spot, TAPI
    price > 5% above VWAP sering reversal. Price below VWAP juga
    tidak selalu bearish (bisa mean reversion opportunity).
    
    Cap maksimal 75 untuk mencegah over-inflasi score.
    """
    pct = row.get("pct_vs_vwap", 0)
    if pd.isna(pct):
        return 40

    # Price di atas VWAP (bullish bias)
    if 0 < pct <= 2:
        return 75       # sweet spot — baru mulai naik
    elif 2 < pct <= 4:
        return 65       # masih aman
    elif 4 < pct <= 7:
        return 45       # extended — risiko pullback
    elif pct > 7:
        return 20       # terlalu tinggi
    
    # Price di bawah VWAP (bearish or mean reversion)
    elif -2 <= pct < 0:
        return 45       # sedikit di bawah, masih wajar
    elif -4 <= pct < -2:
        return 35       # bearish
    elif pct < -4:
        return 15       # strongly bearish
    
    return 40


# ════════════════════════════════════════════════════════════════
#  5. RSI — Reduced weight, more conservative
# ════════════════════════════════════════════════════════════════
def score_rsi(row: pd.Series) -> float:
    """
    RSI scoring v3: LEBIH SEDERHANA, lebih konservatif.
    
    Backtest insight 21.411 sinyal:
    - RSI < 30: falling knife → buruk
    - RSI 40-55 + rising: sweet spot
    - RSI > 65: extended → buruk
    - RSI 55-65: zona abu-abu, tergantung konteks lain
    
    Cap maksimal 70.
    """
    rsi = row.get("rsi", 50)
    stoch_k = row.get("stoch_k", 50)
    stoch_d = row.get("stoch_d", 50)
    
    if pd.isna(rsi) or rsi == 0:
        return 30
    
    # Deteksi arah dari stochastic (proxy)
    rising = (not pd.isna(stoch_k) and not pd.isna(stoch_d)
              and stoch_k > stoch_d)

    # Oversold
    if rsi < 30:
        return 15       # falling knife — avoid
    elif rsi < 35:
        return 25 if rising else 18  # bottoming?
    
    # Lower range
    elif rsi < 40:
        return 50 if rising else 35
    
    # Sweet spot
    elif rsi < 50:
        return 70 if rising else 50   # emerging — best risk/reward
    
    elif rsi < 55:
        return 65 if rising else 50   # good momentum
    
    # Upper range
    elif rsi < 60:
        return 55 if rising else 40   # masih OK
    elif rsi < 65:
        return 35 if rising else 25   # mulai extended
    
    # Overbought
    elif rsi < 70:
        return 25
    else:
        return 15       # overbought — avoid chase


# ════════════════════════════════════════════════════════════════
#  6. MACD — Reduced weight
# ════════════════════════════════════════════════════════════════
def score_macd(row: pd.Series) -> float:
    """
    MACD scoring v3: SIMPEL, bobot kecil.
    
    Backtest: MACD punya predictive power lemah di IDX (random walk).
    Hanya pakai MACD histogram sign + price vs EMA12 confirmation.
    """
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

    if macd_bullish and hist > 0 and ema_confirm:
        return 70   # confirmed
    elif macd_bullish and hist > 0:
        return 55   # mildly bullish
    elif macd_bullish:
        return 45   # crossing
    elif not macd_bullish and hist < 0 and not ema_confirm:
        return 20   # bearish confirmed
    elif not macd_bullish and hist > 0:
        return 35   # potential bottom
    else:
        return 40   # neutral


# ════════════════════════════════════════════════════════════════
#  7. STOCHASTIC — Minor component
# ════════════════════════════════════════════════════════════════
def score_stochastic(row: pd.Series) -> float:
    """
    Stochastic scoring v3: SIMPEL, bobot terkecil.
    
    Hanya bereaksi terhadap kondisi ekstrim (oversold/overbought)
    dan arah (rising/falling).
    """
    k = row.get("stoch_k", 50)
    d = row.get("stoch_d", 50)

    if pd.isna(k) or pd.isna(d):
        return 40

    rising = k > d

    if k < 20:
        return 20       # oversold = weak
    elif k < 30:
        return 50 if rising else 30
    elif k < 50:
        return 65 if rising else 45   # sweet spot
    elif k < 70:
        return 50 if rising else 35
    elif k < 85:
        return 35 if rising else 25
    else:
        return 20       # overbought


# ════════════════════════════════════════════════════════════════
#  COMPUTE TOTAL SCORE — MAIN FUNCTION
# ════════════════════════════════════════════════════════════════
def compute_total_score(row: pd.Series, regime: str = "RANGING") -> float:
    """
    Hitung skor total (0-100) berdasarkan regime.
    
    v3 changes:
    1. Bobot IHSG relative strength ditambahkan (ihsg_rel)
    2. Ceiling per komponen lebih rendah → mencegah score inflation
    3. ADX < 20 → penalty otomatis (trend = prerequisite)
    4. Contradiction detection lebih ketat
    """
    weights = WEIGHTS.get(regime, WEIGHTS["RANGING"])

    s_trend = score_trend(row)
    s_vol = score_volume(row)
    s_ihsg = score_ihsg_relative(row)
    s_vwap = score_vwap(row)
    s_rsi = score_rsi(row)
    s_macd = score_macd(row)
    s_stoch = score_stochastic(row)

    total = (
        s_trend * weights["trend"] +
        s_vol * weights["volume"] +
        s_ihsg * weights["ihsg_rel"] +
        s_vwap * weights["vwap"] +
        s_rsi * weights["rsi"] +
        s_macd * weights["macd"] +
        s_stoch * weights["stoch"]
    )

    # ── ADX Prerequisite Penalty ──
    # Tanpa trend (ADX < 20), score maksimal 55
    adx = row.get("adx", 0)
    if not pd.isna(adx) and adx < 20:
        if total > 55:
            total = 55 + (total - 55) * 0.3  # compress
    
    # ── IHSG Bear Market Penalty ──
    # Jika IHSG sedang bearish, turunkan score
    idx_ret_20d = row.get("idx_ret_20d", 0)
    if not pd.isna(idx_ret_20d) and idx_ret_20d < -3:
        total -= 5
    
    # ── Contradiction Detection ──
    # RSI < 35 + volume spike + MACD bullish = falling knife fakeout
    rsi = row.get("rsi", 50)
    vol_ratio = row.get("vol_ratio", 1.0)
    ret_20d = row.get("ret_20d", 0)
    
    if not pd.isna(rsi) and rsi < 35:
        if not pd.isna(vol_ratio) and vol_ratio > 1.5:
            total -= 5  # falling knife dengan volume = distribusi
        
        if not pd.isna(ret_20d) and ret_20d < -5:
            total -= 4  # sudah turun banyak
    
    # ── Price Extended Penalty ──
    # Jika harga sudah naik > 15% dalam 20 hari, risiko pullback tinggi
    if not pd.isna(ret_20d) and ret_20d > 15:
        total -= 4
    elif not pd.isna(ret_20d) and ret_20d > 10:
        total -= 2

    # ── Conviction bonus (spiky profile) ──
    component_scores = [s_trend, s_vol, s_ihsg, s_vwap, s_rsi, s_macd, s_stoch]
    cp_stdev = statistics.stdev(component_scores)
    
    if cp_stdev >= 15:
        total += 3  # spiky = conviction
    elif cp_stdev < 8:
        total -= 2  # flat = no conviction
    
    # ── Fundamental bonus/penalty ──
    ticker = row.get("ticker", "")
    if ticker:
        total += compute_fundamental_penalty(ticker)

    return round(_norm(total, 0, 100), 1)


# ════════════════════════════════════════════════════════════════
#  THRESHOLDS — ADJUSTED v3
# ════════════════════════════════════════════════════════════════
# Berdasarkan backtest 21.411 sinyal:
#   Grup 60-62: satu-satunya return positif → jadikan BUY zone
#   Grup 62+: return negatif → butuh threshold lebih tinggi
#   Grup < 58: noise
#
# Strategi baru: threshold lebih rapat, lebih banyak HOLD,
# lebih sedikit BUY, dengan harapan win rate naik.
THRESHOLDS = {
    "BULL":            [72, 62, 52, 38],
    "BEAR":            [80, 70, 60, 48],
    "RANGING":         [75, 64, 55, 40],
    "HIGH_VOLATILITY": [75, 64, 55, 40],
}

RISK_CONFIG = {
    "sl_multiplier": 2.0,
    "tp_multiplier": 3.0,
    "sl_floor_pct": 0.85,
}


def configure(cfg: dict):
    """Set threshold + risk config dari config.yaml (dipanggil main.py)."""
    if cfg:
        if "thresholds" in cfg:
            THRESHOLDS.update(cfg["thresholds"])
        if "risk_reward" in cfg:
            RISK_CONFIG.update(cfg["risk_reward"])


def classify(score: float, regime: str = "RANGING") -> str:
    """
    Konversi skor numerik ke sinyal trading.
    v3: threshold lebih ketat untuk mengurangi false signals.
    """
    th = THRESHOLDS.get(regime, THRESHOLDS["RANGING"])
    sb, b, wb, h = th[0], th[1], th[2], th[3]
    if score >= sb:   return "STRONG_BUY"
    elif score >= b:  return "BUY"
    elif score >= wb: return "WEAK_BUY"
    elif score >= h:  return "HOLD"
    else:             return "SELL"


def compute_risk_reward(row: pd.Series) -> dict:
    """
    ATR-based Stop Loss & Take Profit.
    v3: SL/TP multipliers dari RISK_CONFIG.
    """
    price = row.get("close", 0)
    atr = row.get("atr", 0)

    if price == 0 or pd.isna(atr) or atr == 0:
        return {"stop_loss": 0, "take_profit": 0, "rrr": 0.0}

    sl_mul = RISK_CONFIG.get("sl_multiplier", 2.0)
    tp_mul = RISK_CONFIG.get("tp_multiplier", 3.0)
    floor = RISK_CONFIG.get("sl_floor_pct", 0.85)

    sl = round(price - sl_mul * atr, 0)
    tp = round(price + tp_mul * atr, 0)
    risk = price - sl
    reward = tp - price
    rrr = round(reward / risk, 2) if risk > 0 else 0.0

    return {
        "stop_loss": max(sl, int(price * floor)),
        "take_profit": tp,
        "rrr": rrr,
    }


# ════════════════════════════════════════════════════════════════
#  SIGNAL → EXPECTED RETURN
# ════════════════════════════════════════════════════════════════
def expected_return(signal: str) -> float:
    """
    Expected return berdasarkan data backtest terbaru.
    Versi v3: membaca dari CSV backtest terbaru.
    """
    signal = signal.upper()
    try:
        data_dir = os.path.dirname(os.path.abspath(__file__))
        csv_files = glob.glob(os.path.join(data_dir, "backtest_results_*.csv"))
        if csv_files:
            latest = max(csv_files, key=os.path.getmtime)
            df = pd.read_csv(latest)
            sig_df = df[df["signal"] == signal].copy()
            if not sig_df.empty and "avg_return_h5" in sig_df.columns:
                if "count" in sig_df.columns:
                    weighted = (sig_df["avg_return_h5"] * sig_df["count"]).sum() / sig_df["count"].sum()
                else:
                    weighted = sig_df["avg_return_h5"].mean()
                if not pd.isna(weighted) and weighted != 0.0:
                    return round(weighted, 4)
    except Exception:
        logger.warning("Gagal membaca backtest CSV, pakai fallback.")

    # Fallback v3 — akan diupdate setelah backtest baru
    fallback = {
        "STRONG_BUY": -0.43,
        "BUY":        -0.28,
        "WEAK_BUY":   -0.45,
        "HOLD":       -0.74,
        "SELL":       -0.30,
    }
    return fallback.get(signal, 0.0)


# ════════════════════════════════════════════════════════════════
#  KELLY CRITERION
# ════════════════════════════════════════════════════════════════
def calculate_kelly(row: pd.Series, signal: str) -> float:
    """Kelly Criterion: f* = (p * b - q) / b"""
    if signal.upper() not in ("STRONG_BUY", "BUY", "WEAK_BUY"):
        return 0.0

    win_rate = row.get("win_rate_backtest", 0.50)
    if pd.isna(win_rate) or win_rate <= 0 or win_rate >= 1:
        return 0.0

    rr_info = compute_risk_reward(row)
    b = rr_info.get("rrr", 1.5)
    if b <= 0:
        b = 1.5

    p = win_rate
    q = 1.0 - p
    kelly_raw = (p * b - q) / b
    if kelly_raw <= 0:
        return 0.0

    return round(min(kelly_raw * 0.5, 1.0), 4)


# ════════════════════════════════════════════════════════════════
#  QUALITY GATE — Post-scoring filter
# ════════════════════════════════════════════════════════════════
def quality_gate(row: pd.Series, signal: str) -> str:
    """
    Post-scoring quality filter v3.
    Menangkap false breakout, falling knife, low liquidity.
    """
    rsi = row.get("rsi", 50)
    vol_ratio = row.get("vol_ratio", 1.0)
    ret_20d = row.get("ret_20d", 0)
    atr = row.get("atr", 0)
    price = row.get("close", 1)
    adx = row.get("adx", 0)

    if pd.isna(rsi) or pd.isna(vol_ratio) or pd.isna(ret_20d):
        return signal

    downgrade = {"STRONG_BUY": "BUY", "BUY": "WEAK_BUY",
                  "WEAK_BUY": "HOLD", "HOLD": "SELL", "SELL": "SELL"}

    # 1. Falling knife: oversold + volume spike
    falling_knife = (
        pd.notna(rsi) and rsi < 35
        and pd.notna(vol_ratio) and vol_ratio > 1.5
        and pd.notna(ret_20d) and ret_20d < -3.0
    )

    # 2. Low liquidity: ATR < 0.3% of price
    low_liquidity = (
        pd.notna(atr) and pd.notna(price) and price > 0
        and (atr / price * 100) < 0.3
    )

    # 3. No trend: ADX < 15
    no_trend = (
        pd.notna(adx) and adx < 15
    )

    # 4. False breakout: price up 8%+ with below-average volume
    false_breakout = (
        pd.notna(ret_20d) and ret_20d > 8.0
        and pd.notna(vol_ratio) and vol_ratio < 1.0
    )

    if low_liquidity:
        return "HOLD"

    if falling_knife:
        if signal in ("STRONG_BUY", "BUY", "WEAK_BUY", "HOLD"):
            return "SELL"
        return signal

    if no_trend and signal in ("STRONG_BUY", "BUY"):
        return downgrade.get(signal, signal)

    if false_breakout and signal in ("STRONG_BUY", "BUY", "WEAK_BUY"):
        return downgrade.get(signal, signal)

    return signal


def classify_with_gate(row: pd.Series, score: float, regime: str = "RANGING") -> str:
    """classify() + quality_gate() dalam satu langkah."""
    signal = classify(score, regime)
    return quality_gate(row, signal)


# ════════════════════════════════════════════════════════════════
#  FUNDAMENTAL SCORING
# ════════════════════════════════════════════════════════════════
FUNDAMENTALS: dict = {}

def load_fundamentals(csv_path: str) -> dict:
    """Load fundamental data dari hasil scan CSV."""
    global FUNDAMENTALS
    import csv
    FUNDAMENTALS = {}
    count = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("ticker", "").strip()
            if not ticker:
                continue
            try:
                pe = safe_float(row.get("pe_ratio"))
                pbv = safe_float(row.get("pbv"))
                roe = safe_float(row.get("roe"))
                rev_growth = safe_float(row.get("revenue_growth"))
                pm = safe_float(row.get("profit_margin"))
                dy = safe_float(row.get("dividend_yield"))
                FUNDAMENTALS[ticker] = {
                    "pe": pe, "pbv": pbv, "roe": roe,
                    "rev_growth": rev_growth, "pm": pm, "dy": dy,
                }
                count += 1
            except (ValueError, TypeError):
                continue
    return FUNDAMENTALS


def safe_float(val) -> float:
    """Convert value to float, return NaN on failure."""
    if val is None or val == "" or val == "-":
        return float("nan")
    try:
        v = float(str(val).replace(",", "").replace(" ", ""))
        return v
    except (ValueError, TypeError):
        return float("nan")


def score_fundamental(ticker: str) -> float:
    """Score fundamental 0-100 berdasarkan PE, PBV, ROE, revenue_growth."""
    if ticker not in FUNDAMENTALS:
        return 50.0

    f = FUNDAMENTALS.get(ticker, {})
    pe = f.get("pe", float("nan"))
    pbv = f.get("pbv", float("nan"))
    roe = f.get("roe", float("nan"))
    rev = f.get("rev_growth", float("nan"))

    scores = []
    weights = []

    # PE ratio
    if not pd.isna(pe):
        if 8 <= pe <= 15:
            scores.append(85)
        elif 15 < pe <= 25:
            scores.append(65)
        elif 5 <= pe < 8:
            scores.append(55)
        elif 25 < pe <= 40:
            scores.append(40)
        elif pe < 5:
            scores.append(20)
        elif pe < 0:
            scores.append(15)
        else:
            scores.append(30)
        weights.append(0.25)

    # PBV
    if not pd.isna(pbv):
        if 0.8 <= pbv <= 2.0:
            scores.append(80)
        elif 2.0 < pbv <= 4.0:
            scores.append(55)
        elif 0.5 <= pbv < 0.8:
            scores.append(60)
        elif 4.0 < pbv <= 8.0:
            scores.append(30)
        elif pbv < 0.5:
            scores.append(30)
        elif pbv < 0:
            scores.append(10)
        else:
            scores.append(15)
        weights.append(0.25)

    # ROE
    if not pd.isna(roe):
        roe_pct = roe * 100
        if roe_pct > 30:
            scores.append(90)
        elif roe_pct > 20:
            scores.append(80)
        elif roe_pct > 15:
            scores.append(70)
        elif roe_pct > 10:
            scores.append(55)
        elif roe_pct > 5:
            scores.append(35)
        elif roe_pct > 0:
            scores.append(20)
        else:
            scores.append(10)
        weights.append(0.25)

    # Revenue growth
    if not pd.isna(rev):
        rev_pct = rev * 100
        if rev_pct > 30:
            scores.append(85)
        elif rev_pct > 15:
            scores.append(75)
        elif rev_pct > 5:
            scores.append(60)
        elif rev_pct > 0:
            scores.append(45)
        elif rev_pct > -5:
            scores.append(30)
        elif rev_pct > -15:
            scores.append(20)
        else:
            scores.append(10)
        weights.append(0.25)

    if not scores:
        return 50.0

    total_w = sum(weights)
    if total_w == 0:
        return 50.0

    result = sum(s * w for s, w in zip(scores, weights)) / total_w
    return round(result, 1)


def compute_fundamental_penalty(ticker: str) -> float:
    """
    Hitung fundamental bonus/penalty sebagai offset (+5/-5).
    Fungsi ini dipanggil dari compute_total_score().
    """
    fscore = score_fundamental(ticker)
    if fscore >= 65:
        return 3.0
    elif fscore >= 50:
        return 0.0
    elif fscore >= 35:
        return -3.0
    else:
        return -5.0


# ════════════════════════════════════════════════════════════════
#  POSITION QUALITY (0-100)
# ════════════════════════════════════════════════════════════════
def position_quality(row: pd.Series, signal: str) -> float:
    """
    Skor kualitas posisi 0-100.
    Kombinasi signal strength (40%), risk score (30%), volume score (30%).
    """
    exp_ret = expected_return(signal)
    signal_score = _norm((exp_ret + 0.02) / 0.05 * 100, 0, 100)
    risk_score = score_volatility(row)
    volume_score = score_volume(row)

    quality = (
        0.40 * signal_score +
        0.30 * risk_score +
        0.30 * volume_score
    )
    return round(_norm(quality, 0, 100), 1)


# ════════════════════════════════════════════════════════════════
#  LEGACY — volatility scoring (dibutuhkan oleh position_quality)
# ════════════════════════════════════════════════════════════════
def score_volatility(row: pd.Series) -> float:
    """Volatility scoring: 0-100. Low volatility = better."""
    atr = row.get("atr", 0)
    price = row.get("close", 1)
    bb_width = row.get("bb_width_pct", 0)

    if pd.isna(atr) or pd.isna(bb_width) or price == 0:
        return 40

    atr_pct = (atr / price) * 100
    if atr_pct < 0.1:
        return 20

    if atr_pct < 1.5 and bb_width < 15:
        return 85
    elif atr_pct < 2.5 and bb_width < 25:
        return 65
    elif atr_pct < 4.0 and bb_width < 35:
        return 45
    else:
        return 20


# ════════════════════════════════════════════════════════════════
#  SWING SCORE
# ════════════════════════════════════════════════════════════════
def score_swing_setup(row: pd.Series, weekly_trend: str = None,
                       volatility_regime: str = "NORMAL",
                       support_resistance: dict = None) -> float:
    """Skor kualitas setup SWING TRADING (0-100)."""
    score = 50.0

    # 1. Trend alignment (35%)
    trend_alignment = 50
    ema12 = row.get("ema12", 0)
    ema50 = row.get("ema50", 0)
    price = row.get("close", 0)
    adx = row.get("adx", 0)

    if price > 0 and ema12 > 0 and ema50 > 0:
        if price > ema12 > ema50:
            trend_alignment = 85
        elif price > ema12 and ema12 < ema50:
            trend_alignment = 60
        elif price > ema50:
            trend_alignment = 50
        else:
            trend_alignment = 25

    if weekly_trend == "UP" and trend_alignment >= 60:
        trend_alignment = min(trend_alignment + 10, 100)
    elif weekly_trend == "DOWN":
        trend_alignment = max(trend_alignment - 15, 0)

    if adx > 30:
        trend_alignment = min(trend_alignment + 5, 100)
    elif adx < 20:
        trend_alignment = max(trend_alignment - 5, 0)

    # 2. S/R distance (30%)
    sr_score = 50
    if support_resistance:
        support = support_resistance.get("support", 0)
        resistance = support_resistance.get("resistance", 0)
        if support > 0 and resistance > 0 and price > 0:
            dist_to_support = (price - support) / price * 100
            dist_to_resistance = (resistance - price) / price * 100
            if dist_to_support < 2.0 and dist_to_resistance > 3.0:
                sr_score = 85
            elif dist_to_support < 3.0 and dist_to_resistance > 2.0:
                sr_score = 70
            elif dist_to_support < 5.0:
                sr_score = 55
            else:
                sr_score = 35
    else:
        atr = row.get("atr", 0)
        if atr > 0 and price > 0:
            atr_pct = (atr / price) * 100
            if 1.0 <= atr_pct <= 3.0:
                sr_score = 65
            elif atr_pct < 1.0:
                sr_score = 40
            else:
                sr_score = 45

    # 3. Volume conviction (20%)
    vol_score = 50
    vol_ratio = row.get("vol_ratio", 1.0)
    ret_20d = row.get("ret_20d", 0)
    if vol_ratio > 1.5 and ret_20d > 0:
        vol_score = 85
    elif vol_ratio > 1.2 and ret_20d > 0:
        vol_score = 70
    elif vol_ratio > 1.0 and ret_20d > 0:
        vol_score = 55
    elif vol_ratio > 1.5 and ret_20d < 0:
        vol_score = 25
    else:
        vol_score = 40

    # 4. Volatility fit (15%)
    vola_fit = 50
    if volatility_regime == "NORMAL":
        vola_fit = 80
    elif volatility_regime == "LOW":
        vola_fit = 60
    else:
        vola_fit = 30

    final = (trend_alignment * 0.35 + sr_score * 0.30 +
             vol_score * 0.20 + vola_fit * 0.15)
    return round(min(max(final, 0), 100), 1)


def score_trend_strength(trend_strength: float) -> float:
    """Mapping trend strength 0-100 ke skor 0-100."""
    score = min(trend_strength * 2, 100)
    return round(min(max(score, 0), 100), 1)


# ════════════════════════════════════════════════════════════════
#  EARNINGS BLACKOUT — Hindari beli sebelum laporan keuangan
# ════════════════════════════════════════════════════════════════
EARNINGS_CALENDAR = {}  # Diisi oleh load_earnings_calendar()

def load_earnings_calendar(path: str = None) -> dict:
    """
    Load earnings calendar dari CSV.
    Format CSV: ticker,report_date (YYYY-MM-DD)
    
    Return dict: {ticker: [list_of_dates]}
    """
    global EARNINGS_CALENDAR
    if path is None:
        data_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(data_dir, "data", "earnings_calendar.csv")
    
    if not os.path.exists(path):
        logger.info("Earnings calendar tidak ditemukan di %s — blackout skip", path)
        return {}
    
    cal = {}
    try:
        df = pd.read_csv(path)
        required = {"ticker", "report_date"}
        if not required.issubset(df.columns):
            logger.warning("Earnings calendar butuh kolom: ticker, report_date")
            return {}
        for _, row in df.iterrows():
            tkr = str(row["ticker"]).upper().replace(".JK", "")
            date_str = str(row["report_date"]).strip()
            if tkr not in cal:
                cal[tkr] = []
            cal[tkr].append(date_str)
        EARNINGS_CALENDAR = cal
        logger.info("Earnings calendar loaded: %d ticker", len(cal))
    except Exception as e:
        logger.warning("Gagal load earnings calendar: %s", e)
        return {}
    return cal


def is_earnings_blackout(ticker: str, current_date=None,
                         blackout_days: int = 7) -> bool:
    """
    Cek apakah ticker sedang dalam earnings blackout.
    True = jangan entry (earnings dalam X hari ke depan).
    
    Parameters
    ----------
    ticker : str (tanpa .JK)
    current_date : date, optional (default today)
    blackout_days : int, default 7
    
    Returns
    -------
    bool: True jika blackout aktif
    """
    if not EARNINGS_CALENDAR:
        return False  # No calendar loaded, skip check
    
    if current_date is None:
        current_date = date.today()
    
    clean_tkr = ticker.upper().replace(".JK", "")
    report_dates = EARNINGS_CALENDAR.get(clean_tkr, [])
    if not report_dates:
        return False
    
    for date_str in report_dates:
        try:
            report_date = datetime.strptime(str(date_str).strip()[:10], "%Y-%m-%d").date()
            days_until = (report_date - current_date).days
            if 0 <= days_until <= blackout_days:
                return True  # Dalam blackout window
        except (ValueError, TypeError):
            continue
    
    return False
