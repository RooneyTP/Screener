# src/signals/swing_strategy.py — Swing Trading Signal Generator
# FIX: Extracted from screener.py analisis_saham() god function
# Single responsibility: compute swing signal from processed data

from typing import Optional
import numpy as np
import pandas as pd


def compute_swing_entry_sl_tp(
    price: float,
    atr_v: float,
    regime: str,
    weekly_bullish: bool,
    monthly_bullish: bool,
) -> dict:
    """ATR-based Stop Loss and Take Profit for swing trades."""
    risk_factor = 1.5 if regime == "HIGH_VOLATILITY" else 1.2
    base_sl = price - (risk_factor * atr_v)
    if weekly_bullish and monthly_bullish:
        base_sl = price - (risk_factor * atr_v * 1.3)
    stop_loss = max(base_sl, price * 0.92)
    target_1 = price + (2.0 * atr_v)
    target_2 = price + (3.5 * atr_v)
    target_3 = price + (5.0 * atr_v)

    risk_pct = round(((price - stop_loss) / price) * 100, 1)
    reward_pct = round(((target_1 - price) / price) * 100, 1)
    rrr = round(reward_pct / risk_pct, 2) if risk_pct != 0 else 0

    return {
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
        "rrr": rrr,
    }


def compute_multi_timeframe_bullish(close_daily: pd.Series, data_weekly: pd.DataFrame, data_monthly: pd.DataFrame) -> tuple[bool, bool]:
    """Check weekly and monthly bullish alignment."""
    from ta.trend import EMAIndicator
    weekly_bullish = monthly_bullish = False
    try:
        if not data_weekly.empty and len(data_weekly) >= 20:
            close_w = data_weekly["Close"].squeeze()
            ema20_w = EMAIndicator(close=close_w, window=20).ema_indicator()
            weekly_bullish = float(close_w.iloc[-1]) > float(ema20_w.iloc[-1])
    except:
        pass
    try:
        if not data_monthly.empty and len(data_monthly) >= 12:
            close_m = data_monthly["Close"].squeeze()
            ema12_m = EMAIndicator(close=close_m, window=12).ema_indicator()
            monthly_bullish = float(close_m.iloc[-1]) > float(ema12_m.iloc[-1])
    except:
        pass
    return weekly_bullish, monthly_bullish


def market_regime_swing(close: pd.Series, atr: pd.Series) -> str:
    """Classify market regime for swing trading."""
    avg_atr = atr.tail(20).mean()
    current_atr = atr.iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    if current_atr > avg_atr * 1.5:
        return "HIGH_VOLATILITY"
    elif abs(sma20 - sma50) / sma50 > 0.05:
        return "TRENDING"
    return "RANGING"
