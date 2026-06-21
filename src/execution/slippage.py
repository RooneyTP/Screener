# src/execution/slippage.py — ATR-based slippage model (SKILL.md §④)
# FIX: Replace static 0.10% with ATR-adaptive slippage for scalping

import pandas as pd
import numpy as np


def atr_slippage(atr_series: pd.Series, price: float, base_slippage_pct: float = 0.001) -> float:
    """Calculate ATR-adjusted slippage.
    
    For scalping: minimum 0.5× spread, ATR-based during volatile sessions.
    Returns slippage as a decimal fraction (e.g., 0.002 = 0.2%).
    """
    if len(atr_series) < 14:
        return base_slippage_pct
    
    current_atr = atr_series.iloc[-1]
    avg_atr = atr_series.tail(20).mean()
    
    if avg_atr <= 0 or price <= 0:
        return base_slippage_pct
    
    # ATR as % of price
    atr_pct = current_atr / price
    
    # Base: 0.5× average spread (proxy via ATR %)
    base = max(0.0005, atr_pct * 0.5)
    
    # If volatility is elevated (> 1.5× average ATR), widen slippage
    volatility_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0
    multiplier = 1.0 + max(0, (volatility_ratio - 1.0) * 0.5)
    
    return round(float(base * multiplier), 6)


def spread_estimate(high: pd.Series, low: pd.Series, close: pd.Series) -> float:
    """Estimate average spread from OHLC data (proxy: avg(High-Low)/Close)."""
    if len(close) < 5:
        return 0.002  # default 0.2%
    spreads = ((high - low) / close.replace(0, np.nan)).dropna()
    return float(max(0.001, spreads.tail(20).mean()))
