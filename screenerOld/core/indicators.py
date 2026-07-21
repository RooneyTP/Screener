"""
indicators.py — Technical Analysis Indicators for Screener
============================================================
Merged from indicators.py + indicator functions from screener.py.
All functions are pure pandas/numpy calculations with no external
dependencies beyond pandas, numpy, and the `ta` library.
"""

import numpy as np
import pandas as pd

# Auto-install ta if missing — prevents import failure at module level
try:
    from ta.trend import SMAIndicator, EMAIndicator, MACD, ADXIndicator
    from ta.momentum import RSIIndicator, StochasticOscillator
    from ta.volatility import BollingerBands, AverageTrueRange
    from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice
except ImportError:
    import sys, os
    os.system(f"{sys.executable} -m pip install ta -q")
    from ta.trend import SMAIndicator, EMAIndicator, MACD, ADXIndicator
    from ta.momentum import RSIIndicator, StochasticOscillator
    from ta.volatility import BollingerBands, AverageTrueRange
    from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice


# ═══════════════════════════════════════════════════════════════════════════
# BASIC INDICATORS (from original indicators.py)
# ═══════════════════════════════════════════════════════════════════════════

def calculate_sma(data: pd.Series, window: int = 20) -> pd.Series:
    return SMAIndicator(close=data, window=window).sma_indicator()


def calculate_ema(data: pd.Series, window: int = 21) -> pd.Series:
    return EMAIndicator(close=data, window=window).ema_indicator()


def calculate_rsi(data: pd.Series, window: int = 14) -> pd.Series:
    return RSIIndicator(close=data, window=window).rsi()


def calculate_macd(data: pd.Series) -> tuple:
    macd_ind = MACD(close=data)
    return macd_ind.macd(), macd_ind.macd_signal(), macd_ind.macd_diff()


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    return ADXIndicator(high=high, low=low, close=close, window=window).adx()


def calculate_bollinger_bands(data: pd.Series, window: int = 20, window_dev: int = 2) -> tuple:
    bb = BollingerBands(close=data, window=window, window_dev=window_dev)
    return bb.bollinger_mavg(), bb.bollinger_hband(), bb.bollinger_lband()


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    return AverageTrueRange(high=high, low=low, close=close, window=window).average_true_range()


def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume()


def calculate_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    try:
        return VolumeWeightedAveragePrice(high=high, low=low, close=close, volume=volume).volume_weighted_average_price()
    except Exception:
        return close  # Fallback


# ═══════════════════════════════════════════════════════════════════════════
# ADVANCED INDICATORS (from screener.py)
# ═══════════════════════════════════════════════════════════════════════════

def hma(data: pd.Series, period: int = 20) -> pd.Series:
    """Hull Moving Average yang benar menggunakan WMA."""
    def _wma(series: pd.Series, length: int) -> pd.Series:
        weights = np.arange(1, length + 1, dtype=float)
        return series.rolling(length).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )
    half_period = max(1, int(period / 2))
    sqrt_period = max(1, int(np.sqrt(period)))
    raw_hma = 2 * _wma(data, half_period) - _wma(data, period)
    return _wma(raw_hma, sqrt_period)


def detect_support_resistance(data: pd.Series, lookback: int = 20) -> tuple:
    support = data.rolling(lookback).min().iloc[-1]
    resistance = data.rolling(lookback).max().iloc[-1]
    return support, resistance


def market_regime(close: pd.Series, atr: pd.Series) -> str:
    avg_atr = atr.tail(20).mean()
    current_atr = atr.iloc[-1]

    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]

    if current_atr > avg_atr * 1.5:
        return "HIGH_VOLATILITY"
    elif abs(sma20 - sma50) / sma50 > 0.05:
        return "TRENDING"
    else:
        return "RANGING"


def volume_analysis(volume: pd.Series, close: pd.Series) -> float:
    vol_trend = volume.pct_change(10).mean() * 100
    strength = max(0, min(100, 50 + vol_trend))
    return round(strength, 1)


def volume_price_trend(close: pd.Series, volume: pd.Series) -> pd.Series:
    price_change_pct = close.pct_change().fillna(0)
    vpt = (volume * price_change_pct).cumsum()
    return vpt


def chaikin_money_flow(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 21) -> pd.Series:
    mfv = ((close - low) - (high - close)) / (high - low) * volume
    mfv = mfv.replace([np.inf, -np.inf], 0).fillna(0)
    cmf = mfv.rolling(window).sum() / volume.rolling(window).sum()
    return cmf


def ease_of_movement(high: pd.Series, low: pd.Series, volume: pd.Series, window: int = 14) -> pd.Series:
    distance = ((high + low) / 2) - ((high.shift(1) + low.shift(1)) / 2)
    price_range = (high - low).clip(lower=0.0001)
    box_ratio = (volume / 100000000) / price_range
    # Guard: box_ratio == 0 -> avoid division by zero in emv
    box_ratio = box_ratio.replace(0, np.nan)
    emv = distance / box_ratio
    emv = emv.replace([np.inf, -np.inf], np.nan).fillna(0)
    return emv.rolling(window).mean().fillna(0)


def volume_oscillator(volume: pd.Series, short_window: int = 5, long_window: int = 10) -> pd.Series:
    short_ma = volume.rolling(short_window).mean()
    long_ma = volume.rolling(long_window).mean()
    return ((short_ma - long_ma) / long_ma.replace(0, np.nan)).fillna(0) * 100


def accumulation_distribution(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    mfm = ((close - low) - (high - close)) / (high - low)
    mfm = mfm.replace([np.inf, -np.inf], 0).fillna(0)
    mfv = mfm * volume
    ad = mfv.cumsum()
    return ad


def detect_zscore_anomaly(series: pd.Series, window: int = 60) -> float:
    """Mendeteksi apakah volume hari ini adalah anomali ekstrem (Z-Score > 3)"""
    if len(series) < window:
        return 0.0
    mean = series.rolling(window).mean().iloc[-1]
    std = series.rolling(window).std().iloc[-1]
    if std == 0:
        return 0.0
    return float((series.iloc[-1] - mean) / std)


def detect_divergence(prices: pd.Series, rsi: pd.Series, lookback: int = 5) -> str:
    if len(prices) < lookback + 10 or len(rsi) < lookback + 10:
        return "NONE"

    recent_price_min = prices.tail(lookback).min()
    prev_price_min = prices.iloc[-lookback-10:-lookback].min()

    recent_rsi_min = rsi.tail(lookback).min()
    prev_rsi_min = rsi.iloc[-lookback-10:-lookback].min()

    if recent_price_min < prev_price_min and recent_rsi_min > prev_rsi_min:
        return "BULLISH_DIV"
    elif recent_price_min > prev_price_min and recent_rsi_min < prev_rsi_min:
        return "BEARISH_DIV"

    return "NONE"


def calculate_ichimoku_cloud(high: pd.Series, low: pd.Series, close: pd.Series) -> dict:
    """
    Full Ichimoku Cloud with 5-component signal (v11).
    Includes: TK cross, Kumo breakout, Cloud color, Chikou Span.
    """
    # 1. Tenkan-sen (Conversion Line) -- 9 periods
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    # 2. Kijun-sen (Base Line) -- 26 periods
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    # 3. Senkou Span A -- Shifted forward 26 periods
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    # 4. Senkou Span B -- 52 periods, shifted forward 26 periods
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    # 5. Chikou Span -- Close shifted BACK 26 periods
    chikou = close.shift(-26)

    # -- 5-Component Signal --
    signals = []
    strength = 0

    # A. Kumo (Cloud) Position: harga vs cloud
    if pd.notna(senkou_a.iloc[-1]) and pd.notna(senkou_b.iloc[-1]):
        cloud_top = senkou_a.iloc[-1] if senkou_a.iloc[-1] > senkou_b.iloc[-1] else senkou_b.iloc[-1]
        cloud_bot = senkou_b.iloc[-1] if senkou_a.iloc[-1] > senkou_b.iloc[-1] else senkou_a.iloc[-1]

        if close.iloc[-1] > cloud_top:
            signals.append("KUMO_ABOVE")
            strength += 2
        elif close.iloc[-1] < cloud_bot:
            signals.append("KUMO_BELOW")
            strength -= 2
        else:
            signals.append("IN_KUMO")
            strength += 0

    # B. Cloud Color: Senkou_A > Senkou_B = Bullish (green), vice versa (red)
    if pd.notna(senkou_a.iloc[-1]) and pd.notna(senkou_b.iloc[-1]):
        if senkou_a.iloc[-1] > senkou_b.iloc[-1]:
            signals.append("CLOUD_GREEN")
            strength += 1
        else:
            signals.append("CLOUD_RED")
            strength -= 1

    # C. TK Cross: Tenkan crosses Kijun
    if pd.notna(tenkan.iloc[-1]) and pd.notna(kijun.iloc[-1]) and pd.notna(tenkan.iloc[-2]) and pd.notna(kijun.iloc[-2]):
        if tenkan.iloc[-1] > kijun.iloc[-1] and tenkan.iloc[-2] <= kijun.iloc[-2]:
            signals.append("TK_GOLDEN_CROSS")
            strength += 3
        elif tenkan.iloc[-1] < kijun.iloc[-1] and tenkan.iloc[-2] >= kijun.iloc[-2]:
            signals.append("TK_DEATH_CROSS")
            strength -= 3
        elif tenkan.iloc[-1] > kijun.iloc[-1]:
            signals.append("TK_ABOVE")
            strength += 1
        else:
            signals.append("TK_BELOW")
            strength -= 1

    # D. Chikou Span vs Price (lagging confirmation)
    if pd.notna(chikou.iloc[-1]) and pd.notna(close.iloc[-26]):
        if chikou.iloc[-1] > close.iloc[-26]:
            signals.append("CHIKOU_ABOVE")
            strength += 1
        else:
            signals.append("CHIKOU_BELOW")
            strength -= 1

    # E. Overall Composite Signal
    if strength >= 3:
        composite = "BULLISH"
    elif strength <= -3:
        composite = "BEARISH"
    elif strength >= 1:
        composite = "MILD_BULLISH"
    elif strength <= -1:
        composite = "MILD_BEARISH"
    else:
        composite = "NEUTRAL"

    return {
        "tenkan": float(tenkan.iloc[-1]) if pd.notna(tenkan.iloc[-1]) else 0,
        "kijun": float(kijun.iloc[-1]) if pd.notna(kijun.iloc[-1]) else 0,
        "senkou_a": float(senkou_a.iloc[-1]) if pd.notna(senkou_a.iloc[-1]) else 0,
        "senkou_b": float(senkou_b.iloc[-1]) if pd.notna(senkou_b.iloc[-1]) else 0,
        "chikou": float(chikou.iloc[-1]) if pd.notna(chikou.iloc[-1]) else 0,
        "signal": composite,
        "signal_strength": strength,
        "components": signals,
        "cloud_color": "GREEN" if "CLOUD_GREEN" in signals else "RED",
    }


def pattern_recognition(close: pd.Series, high: pd.Series, low: pd.Series, lookback: int = 5) -> str:
    if len(close) < max(lookback + 10, 4):
        return "NONE"

    recent_high = high.tail(lookback).max()
    prev_high = high.iloc[-lookback-10:-lookback].max()
    resistance_broken = close.iloc[-1] > prev_high and close.iloc[-2] <= prev_high

    if resistance_broken:
        return "BREAKOUT"

    if len(close) >= 3 and close.iloc[-1] > close.iloc[-2] * 1.02 and close.iloc[-2] < close.iloc[-3]:
        return "REVERSAL"

    if len(close) >= 2 and close.iloc[-1] > close.iloc[-2::-1].min():
        return "CONTINUATION"

    return "NONE"


# ═══════════════════════════════════════════════════════════════════════════
# MARKET MAKER ANALYSIS FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def detect_market_maker_activity(
    close: pd.Series, volume: pd.Series, vpt: pd.Series, cmf: pd.Series,
    ad: pd.Series, vwap_deviation: float = 0, cumulative_delta: float = 0
) -> dict:
    vol_ma20 = volume.rolling(20).mean()
    recent_vol = volume.tail(5).mean()
    avg_vol = vol_ma20.iloc[-1]

    price_ma20 = close.rolling(20).mean()
    price_trend = (close.iloc[-1] - price_ma20.iloc[-1]) / price_ma20.iloc[-1] * 100

    vpt_trend = vpt.iloc[-1] - vpt.iloc[-20] if len(vpt) > 20 else 0
    cmf_current = cmf.iloc[-1] if not pd.isna(cmf.iloc[-1]) else 0
    cmf_prev = cmf.iloc[-5:-1].mean() if len(cmf) > 5 else 0
    ad_trend = ad.iloc[-1] - ad.iloc[-20] if len(ad) > 20 else 0
    vol_spike = recent_vol > avg_vol * 1.5

    accumulation_signals = []
    distribution_signals = []

    if vol_spike and abs(price_trend) < 2:
        accumulation_signals.append("VOL_SPIKE_STABLE")

    if vpt_trend > 0 and recent_vol > avg_vol * 0.8:
        accumulation_signals.append("VPT_RISING")

    if cmf_current > 0.1 and cmf_current > cmf_prev:
        accumulation_signals.append("CMF_POSITIVE")

    if ad_trend > 0:
        accumulation_signals.append("AD_RISING")

    if vwap_deviation > 0 and cumulative_delta > 0:
        accumulation_signals.append("VWAP_ABOVE_DELTA_POS")

    if vol_spike and price_trend < -1:
        distribution_signals.append("VOL_SPIKE_DOWN")

    if vpt_trend < 0:
        distribution_signals.append("VPT_FALLING")

    if cmf_current < -0.1 and cmf_current < cmf_prev:
        distribution_signals.append("CMF_NEGATIVE")

    if ad_trend < 0:
        distribution_signals.append("AD_FALLING")

    if vwap_deviation < 0 and cumulative_delta < 0:
        distribution_signals.append("VWAP_BELOW_DELTA_NEG")

    acc_score = len(accumulation_signals)
    dist_score = len(distribution_signals)

    if acc_score > dist_score and acc_score >= 2:
        activity = "ACCUMULATION"
        confidence = min(90, acc_score * 20)
    elif dist_score > acc_score and dist_score >= 2:
        activity = "DISTRIBUTION"
        confidence = min(90, dist_score * 20)
    else:
        activity = "NEUTRAL"
        confidence = 50

    return {
        "activity": activity,
        "confidence": int(confidence),
        "accumulation_signals": accumulation_signals,
        "distribution_signals": distribution_signals,
        "volume_spike": bool(vol_spike),
        "vpt_trend": float(vpt_trend),
        "cmf_signal": float(cmf_current),
        "ad_trend": float(ad_trend),
        "vwap_deviation": float(vwap_deviation),
        "cumulative_delta": float(cumulative_delta)
    }


def estimate_market_maker_position(
    close: pd.Series, volume: pd.Series, mm_activity: dict,
    current_price: float, fundamentals: dict | None = None
) -> dict:
    avg_daily_volume = volume.tail(30).mean() if len(volume) >= 30 else volume.mean()

    float_shares = 0
    shares_outstanding = 0
    float_estimated = False

    if fundamentals:
        float_shares = fundamentals.get("float_shares", 0) or 0
        shares_outstanding = fundamentals.get("shares_outstanding", 0) or 0
        float_estimated = fundamentals.get("float_estimated", False)

    if float_shares <= 0 and shares_outstanding > 0:
        float_shares = int(shares_outstanding * 0.25)
        float_estimated = True

    base_position_pct = 0.02
    if mm_activity["activity"] == "ACCUMULATION":
        base_position_pct = 0.025
    elif mm_activity["activity"] == "DISTRIBUTION":
        base_position_pct = 0.015

    activity_multiplier = 1.0
    if mm_activity["activity"] == "ACCUMULATION":
        activity_multiplier = 1.5 + (mm_activity["confidence"] - 50) / 100
    elif mm_activity["activity"] == "DISTRIBUTION":
        activity_multiplier = 0.5 - (mm_activity["confidence"] - 50) / 200

    volume_multiplier = 1.0
    if mm_activity["volume_spike"]:
        volume_multiplier = 1.25

    volume_estimated_shares = int(avg_daily_volume * base_position_pct * activity_multiplier * volume_multiplier)

    if float_shares > 0:
        estimated_shares = max(volume_estimated_shares, int(float_shares * 0.005 * activity_multiplier))
    else:
        estimated_shares = volume_estimated_shares

    if shares_outstanding > 0 and estimated_shares > shares_outstanding:
        estimated_shares = int(shares_outstanding * 0.05)

    position_value = estimated_shares * current_price

    float_base = float_shares if float_shares > 0 else max(int(avg_daily_volume * 250 * 0.4), 1)
    mm_float_pct = (estimated_shares / float_base) * 100 if float_base > 0 else 0

    accumulation_intensity = 0
    if mm_activity["activity"] == "ACCUMULATION":
        accumulation_intensity = len(mm_activity["accumulation_signals"]) * mm_activity["confidence"] / 100
    elif mm_activity["activity"] == "DISTRIBUTION":
        accumulation_intensity = -len(mm_activity["distribution_signals"]) * mm_activity["confidence"] / 100

    return {
        "estimated_shares": estimated_shares,
        "position_value_idr": position_value,
        "float_percentage": round(min(mm_float_pct, 20.0), 2),
        "float_shares": int(float_base) if float_base > 0 else 0,
        "shares_outstanding": int(shares_outstanding),
        "accumulation_intensity": accumulation_intensity,
        "volume_base": int(avg_daily_volume),
        "confidence_adjusted": activity_multiplier,
        "float_estimated": float_estimated,
    }


def estimate_retail_vs_mm_comparison(
    mm_position: dict, market_price: float, fundamentals: dict | None = None
) -> dict:
    mm_shares = mm_position["estimated_shares"]

    float_shares = 0
    shares_outstanding = 0

    if fundamentals:
        float_shares = fundamentals.get("float_shares", 0) or 0
        shares_outstanding = fundamentals.get("shares_outstanding", 0) or 0

    if float_shares <= 0:
        float_shares = mm_position.get("float_shares", 0) or 0

    if float_shares <= 0 and shares_outstanding > 0:
        float_shares = int(shares_outstanding * 0.35)

    if float_shares <= 0:
        float_shares = 100_000

    institutional_shares = int(float_shares * 0.35)

    if mm_shares > float_shares:
        mm_shares = float_shares

    retail_shares = max(0, int(float_shares - institutional_shares - mm_shares))

    if retail_shares == 0:
        mm_vs_retail_ratio = 999.99
    else:
        mm_vs_retail_ratio = (mm_shares / retail_shares) * 100

    mm_vs_float_ratio = (mm_shares / float_shares * 100) if float_shares > 0 else 0

    if mm_vs_retail_ratio > 200:
        dominance = "MM_DOMINANT"
    elif mm_vs_retail_ratio > 100:
        dominance = "MM_STRONG"
    elif mm_vs_retail_ratio > 50:
        dominance = "MM_MODERATE"
    elif mm_vs_retail_ratio > 25:
        dominance = "BALANCED"
    else:
        dominance = "RETAIL_DOMINANT"

    return {
        "retail_shares": retail_shares,
        "retail_value_idr": retail_shares * market_price,
        "institutional_shares": institutional_shares,
        "institutional_value_idr": institutional_shares * market_price,
        "estimated_float_shares": float_shares,
        "estimated_float_value": float_shares * market_price,
        "mm_vs_retail_ratio": round(mm_vs_retail_ratio, 2),
        "mm_vs_float_ratio": round(mm_vs_float_ratio, 2),
        "dominance": dominance,
    }
