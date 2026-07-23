"""
v7_exit.py — Exit strategy untuk V7 dual mode
===============================================
Swing (H+5 hingga H+20): trailing stop + ATR-based TP
Intraday (H+1 hingga H+3): fixed TP + tighter SL + time stop
"""

import numpy as np, pandas as pd
from typing import Optional

def compute_exit(price: float, atr: float, regime: str = "RANGING", 
                 mode: str = "swing", weekly_trend: str = "BULLISH") -> dict:
    """
    Hitung exit strategy berdasarkan mode trading.
    
    Parameters
    ----------
    price : float — harga entry
    atr : float — Average True Range
    regime : str — market regime
    mode : str — "swing" atau "intraday"
    weekly_trend : str — weekly trend
    """
    if price <= 0 or atr <= 0:
        return {"stop_loss": int(price * 0.95), "take_profit": int(price * 1.05),
                "trailing_start": int(price * 1.03), "max_hold_days": 5, "rrr": 1.0}
    
    if mode == "intraday":
        # Intraday: tighter stop, faster profit
        sl_mult = 1.0      # SL lebih dekat
        tp_mult = 1.5      # TP lebih kecil
        trail_activation = 2.0  # trailing aktif lebih cepat
        max_hold = 3       # maksimal 3 hari
        # Intraday: stop loss lebih ketat di trending market
        if regime == "BULL":
            sl_mult = 0.8
            tp_mult = 1.3
        elif regime == "BEAR":
            sl_mult = 1.2
            tp_mult = 1.2  # ambil profit cepat
    else:
        # Swing: lebih longgar
        sl_mult = 1.5
        tp_mult = 2.5
        trail_activation = 3.0
        max_hold = 20
        if weekly_trend == "BULLISH":
            tp_mult = 3.0  # tren bagus, kasih ruang lebih
        elif regime == "BEAR":
            sl_mult = 2.0  # SL lebih lebar di bear
    
    stop_loss = max(int(price * 0.92), int(price - atr * sl_mult))
    take_profit = int(price + atr * tp_mult)
    trailing_start = int(price + atr * trail_activation)
    rrr = (take_profit - price) / max(price - stop_loss, 1) if price - stop_loss > 0 else 1.0
    
    return {
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "trailing_start": trailing_start,
        "max_hold_days": max_hold,
        "rrr": round(rrr, 2),
    }


def get_time_stop(entry_date: str, mode: str = "swing") -> dict:
    """Hitung kapan harus exit berdasarkan waktu."""
    from datetime import datetime, timedelta
    try:
        entry = datetime.strptime(entry_date, "%Y-%m-%d")
    except:
        return {"exit_by": "?", "days_held": 0}
    
    max_days = 20 if mode == "swing" else 3
    exit_date = entry + timedelta(days=max_days)
    today = datetime.now()
    days_held = (today - entry).days if today > entry else 0
    remaining = max(0, max_days - days_held)
    
    if remaining <= 0:
        urgency = "⚠️ OVERDUE — EXIT NOW"
    elif remaining <= 2:
        urgency = f"🟡 EXIT {remaining} hari lagi"
    else:
        urgency = f"✅ Hold ({remaining} hari tersisa)"
    
    return {"exit_by": exit_date.strftime("%d/%m/%Y"), "days_held": days_held,
            "remaining": remaining, "urgency": urgency}


def position_sizing(capital: float, price: float, score: float, atr_pct: float) -> dict:
    """
    Dynamic position sizing — alokasi modal berdasarkan score & volatilitas.
    """
    if capital <= 0 or price <= 0:
        return {"lots": 0, "cost": 0, "risk_pct": 0}
    
    # Score-based allocation
    if score >= 70: base_pct = 0.20     # 20% modal
    elif score >= 62: base_pct = 0.15   # 15%
    elif score >= 55: base_pct = 0.10   # 10%
    elif score >= 48: base_pct = 0.05   # 5%
    else: return {"lots": 0, "cost": 0, "risk_pct": 0}
    
    # Vol adjustment
    if atr_pct > 5: base_pct *= 0.5     # volatil = setengah
    elif atr_pct < 1.5: base_pct *= 1.3 # low vol = lebih berani
    
    cost = capital * base_pct
    lots = max(1, int(cost / (price * 100)))  # 100 saham per lot
    actual_cost = lots * price * 100
    
    return {
        "lots": lots,
        "cost": int(actual_cost),
        "pct_modal": round(base_pct * 100, 1),
        "risk_amount": int(actual_cost * 0.05),  # max loss 5%
    }
