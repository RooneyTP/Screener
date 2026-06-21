# src/execution/sizer.py — Position Sizing Models (SKILL.md §④)
# FIX: Extracted from screener.py — 3 models as specified by SKILL.md

import numpy as np
from typing import Optional


def fixed_fractional(
    account_equity: float,
    risk_pct: float = 0.01,
    entry: float = 0,
    stop_loss: float = 0,
) -> int:
    """Fixed Fractional (recommended default by SKILL.md).
    position_size = (account_equity * risk_per_trade) / (entry - stop_loss)
    """
    if entry <= 0 or stop_loss <= 0 or stop_loss >= entry:
        return 0
    risk_amount = account_equity * risk_pct
    points_at_risk = abs(entry - stop_loss)
    if points_at_risk <= 0:
        return 0
    return int(risk_amount / points_at_risk)


def atr_based(
    account_equity: float,
    risk_pct: float = 0.01,
    atr_value: float = 0,
    atr_multiplier: float = 2.0,
    entry: float = 0,
) -> int:
    """ATR-based sizing — adapts to volatility (SKILL.md).
    stop_distance = atr_multiplier * ATR(14)
    position_size = (account_equity * risk_per_trade) / stop_distance
    """
    if atr_value <= 0 or entry <= 0:
        return 0
    stop_distance = atr_multiplier * atr_value
    risk_amount = account_equity * risk_pct
    if stop_distance <= 0:
        return 0
    return int(risk_amount / stop_distance)


def half_kelly(
    account_equity: float,
    win_rate: float,
    loss_rate: float,
    win_loss_ratio: float,
    entry: float = 0,
) -> tuple[int, float]:
    """Half-Kelly criterion — for strategies with verified edge (SKILL.md).
    kelly_fraction = win_rate - (loss_rate / win_loss_ratio)
    position_size = 0.5 * kelly_fraction * account_equity
    ALWAYS half-Kelly, never full (safety rule).
    """
    if loss_rate <= 0 or win_loss_ratio <= 0 or entry <= 0:
        return 0, 0.0
    kelly_fraction = win_rate - (loss_rate / win_loss_ratio)
    if kelly_fraction <= 0:
        return 0, 0.0
    safe_kelly = min(kelly_fraction / 2.0, 0.25)  # half-Kelly, max 25%
    allocation = account_equity * safe_kelly
    shares = int(allocation / entry)
    return shares, safe_kelly
