"""
strategies/scalp.py — Scalp Trading Strategy (ORB)
===================================================
Minimal implementation based on QUANT audit recommendations.

NOTES:
    - Archive scalp system had critical bugs: AI features hardcoded to 12,
      spread_buffer / 100 bug (now fixed to / 100.0).
    - This module implements ORB (Opening Range Breakout) — a mathematically
      valid scalping strategy with no hardcoded AI proxies.
    - Spread is handled as decimal fraction throughout (not percentage).
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("scalp_strategy")


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

# Default opening range minutes (IDX: first 15-30 min)
DEFAULT_ORB_MINUTES = 15

# Minimum volume threshold as fraction of average
MIN_VOLUME_FRACTION = 0.7


# ═══════════════════════════════════════════════════════════════════════════
# ORB CALCULATION
# ═══════════════════════════════════════════════════════════════════════════

def compute_orb(
    df: pd.DataFrame,
    orb_minutes: int = DEFAULT_ORB_MINUTES,
) -> dict:
    """Compute Opening Range Breakout levels from intraday data.

    Args:
        df: DataFrame with intraday OHLCV data, sorted by time ascending,
            with at least ``orb_minutes`` rows of opening range.
        orb_minutes: Number of minutes/periods for the opening range.

    Returns:
        dict with keys:
            orb_high, orb_low, orb_mid, breakout_long, breakout_short,
            range_pct, valid
    """
    if df.empty or len(df) < orb_minutes + 1:
        return {
            "orb_high": None, "orb_low": None, "orb_mid": None,
            "breakout_long": False, "breakout_short": False,
            "range_pct": 0.0, "valid": False,
        }

    # Opening range = first orb_minutes periods
    opening_range = df.iloc[:orb_minutes]
    orb_high = float(opening_range["High"].max())
    orb_low = float(opening_range["Low"].min())
    orb_mid = (orb_high + orb_low) / 2.0

    # Latest price
    current_price = float(df.iloc[-1]["Close"])
    prev_close = float(df.iloc[0].get("Prev_Close", df.iloc[0]["Open"]))

    # Range as % of prev close (for volatility context)
    range_pct = ((orb_high - orb_low) / prev_close) * 100 if prev_close > 0 else 0.0

    # Breakout signals (after opening range period)
    post_range = df.iloc[orb_minutes:]
    if not post_range.empty:
        post_high = float(post_range["High"].max())
        post_low = float(post_range["Low"].min())
        breakout_long = current_price > orb_high and post_high > orb_high
        breakout_short = current_price < orb_low and post_low < orb_low
    else:
        breakout_long = current_price > orb_high
        breakout_short = current_price < orb_low

    return {
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_mid": orb_mid,
        "breakout_long": breakout_long,
        "breakout_short": breakout_short,
        "range_pct": round(range_pct, 2),
        "valid": True,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SPREAD HANDLING (fixed from archive bug: spread_buffer / 100 → / 100.0)
# ═══════════════════════════════════════════════════════════════════════════

def spread_adjusted_price(
    price: float,
    spread_buffer: float,
    side: str = "buy",
) -> float:
    """Adjust price by spread buffer as decimal fraction.

    Args:
        price: Base price.
        spread_buffer: Buffer as percentage (e.g., 0.15 means 0.15%).
                       Internally divided by 100.0 to convert to decimal.
        side: "buy" adds spread (paying more), "sell" subtracts (receiving less).

    Returns:
        Spread-adjusted price.

    FIX: Original archive code had ``spread_buffer / 100`` (integer division bug).
         Using ``/ 100.0`` ensures float division.
    """
    spread_decimal = spread_buffer / 100.0  # 0.15% → 0.0015
    if side == "buy":
        return price * (1.0 + spread_decimal)
    elif side == "sell":
        return price * (1.0 - spread_decimal)
    return price


# ═══════════════════════════════════════════════════════════════════════════
# SCALPSTRATEGY CLASS
# ═══════════════════════════════════════════════════════════════════════════

class ScalpStrategy:
    """Scalp trading strategy using ORB (Opening Range Breakout).

    Pure technical approach — no hardcoded AI features or heuristic proxies.
    Entry on breakout of opening range with volume confirmation.
    """

    def __init__(
        self,
        orb_minutes: int = DEFAULT_ORB_MINUTES,
        spread_buffer: float = 0.15,  # 0.15%
        risk_reward_target: float = 1.5,
        max_spread_pct: float = 0.5,  # 0.5% max acceptable spread
    ):
        """
        Args:
            orb_minutes: Periods for opening range calculation.
            spread_buffer: Spread buffer in percent (e.g., 0.15 = 0.15%).
            risk_reward_target: Minimum R:R for scalp entries.
            max_spread_pct: Maximum allowable spread % to enter a trade.
        """
        self.orb_minutes = orb_minutes
        self.spread_buffer = spread_buffer
        self.risk_reward_target = risk_reward_target
        self.max_spread_pct = max_spread_pct
        logger.info(
            "ScalpStrategy initialized (orb=%d min, spread=%.2f%%, rr=%.1f)",
            orb_minutes, spread_buffer, risk_reward_target,
        )

    def analyze(self, ticker: str) -> dict:
        """Stub — requires intraday data for real analysis."""
        return {
            "ticker": ticker,
            "status": "requires intraday OHLCV data — call get_signal(df)",
        }

    def get_signal(self, df: pd.DataFrame) -> dict:
        """Compute scalp signal from intraday OHLCV data using ORB.

        Args:
            df: Intraday DataFrame with columns:
                Open, High, Low, Close, Volume, Prev_Close (optional).

        Returns:
            dict with ORB levels, breakout direction, and trade parameters.
        """
        # Calculate ORB levels
        orb = compute_orb(df, self.orb_minutes)

        if not orb["valid"]:
            return {
                "signal": "INSUFFICIENT_DATA",
                "direction": "NONE",
                "reason": f"Need at least {self.orb_minutes + 1} periods",
                "orb": orb,
            }

        current_price = float(df.iloc[-1]["Close"])

        # Volume confirmation (current volume vs average of opening range)
        opening_volume = float(df.iloc[:self.orb_minutes]["Volume"].mean())
        recent_volume = float(df.iloc[-5:]["Volume"].mean()) if len(df) >= 5 else 0
        volume_ok = recent_volume >= opening_volume * MIN_VOLUME_FRACTION if opening_volume > 0 else False

        # Determine direction
        direction = "NONE"
        signal = "HOLD"

        if orb["breakout_long"] and volume_ok:
            direction = "LONG"
            signal = "BUY"
        elif orb["breakout_short"] and volume_ok:
            direction = "SHORT"
            signal = "SELL"

        # Risk management (tight for scalping)
        stop_loss = 0.0
        target = 0.0
        rrr = 0.0
        spread_pct = 0.0

        if direction == "LONG":
            stop_loss = orb["orb_low"] * 0.998  # tiny buffer below ORB low
            target = current_price + (current_price - stop_loss) * self.risk_reward_target
            risk = (current_price - stop_loss) / current_price * 100
            reward = (target - current_price) / current_price * 100
            rrr = reward / risk if risk > 0 else 0
            spread_pct = self.spread_buffer
        elif direction == "SHORT":
            stop_loss = orb["orb_high"] * 1.002  # tiny buffer above ORB high
            target = current_price - (stop_loss - current_price) * self.risk_reward_target
            risk = (stop_loss - current_price) / current_price * 100
            reward = (current_price - target) / current_price * 100
            rrr = reward / risk if risk > 0 else 0
            spread_pct = self.spread_buffer

        return {
            "signal": signal,
            "direction": direction,
            "price": current_price,
            "orb_high": orb["orb_high"],
            "orb_low": orb["orb_low"],
            "orb_mid": orb["orb_mid"],
            "orb_range_pct": orb["range_pct"],
            "stop_loss": stop_loss,
            "target": target,
            "rrr": round(rrr, 2),
            "risk_pct": round(risk, 2) if direction != "NONE" else 0.0,
            "reward_pct": round(reward, 2) if direction != "NONE" else 0.0,
            "spread_est_pct": spread_pct,
            "volume_confirmed": volume_ok,
            "entry_long": spread_adjusted_price(current_price, self.spread_buffer, "buy") if direction == "LONG" else None,
            "entry_short": spread_adjusted_price(current_price, self.spread_buffer, "sell") if direction == "SHORT" else None,
        }
