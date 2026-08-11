"""
quality_gate.py — Post-scoring quality filter.
Menangkap skenario di mana model salah tinggi karena kombinasi indikator
yang misleading (contoh: volume tinggi + harga turun = distribusi).
"""
import pandas as pd
import logging

logger = logging.getLogger("quality_gate")


def quality_gate(row: pd.Series, signal: str) -> str:
    """
    Apply quality filters after scoring. Returns ADJUSTED signal.

    Rules (backtest-proven, Juli 2026):
      1. FALLING KNIFE: RSI < 35 + vol spike > 1.5x + ret_20d < -3%.
         Terjadi distribusi, bukan akumulasi. → downgrade 1 level.
      2. LOW LIQUIDITY: ATR < 0.3% harga = spread lebar, unreliable. → HOLD.
      3. NO TREND (ADX < 15) + low volume (< 1.0x). Pasar choppy,
         sinyal teknikal unreliable. → downgrade 1 level.
      4. LOW VOLUME BREAKOUT: ret_20d > +8% tapi vol < 1.0x.
         Harga naik tanpa konfirmasi volume = false breakout. → downgrade 1 level.
    """
    rsi = row.get("rsi", 50)
    vol_ratio = row.get("vol_ratio", 1.0)
    ret_20d = row.get("ret_20d", 0)
    atr = row.get("atr", 0)
    price = row.get("close", 1)
    adx = row.get("adx", 0)

    if pd.isna(rsi) or pd.isna(vol_ratio) or pd.isna(ret_20d):
        return signal

    # ── Quality check ──────────────────────────────────────────────
    check_falling_knife = False
    check_low_liquidity = False
    check_no_trend = False
    check_false_breakout = False

    # 1. Falling knife: oversold + volume spike + already down
    if (pd.notna(rsi) and rsi < 35
            and pd.notna(vol_ratio) and vol_ratio > 1.5
            and pd.notna(ret_20d) and ret_20d < -3.0):
        check_falling_knife = True
        logger.debug("  ⚠️  Falling knife: RSI=%.1f vol=%.1fx ret_20d=%.1f%%",
                     rsi, vol_ratio, ret_20d)

    # 2. Low liquidity: ATR < 0.3% of price
    if (pd.notna(atr) and pd.notna(price) and price > 0
            and (atr / price * 100) < 0.3):
        check_low_liquidity = True
        logger.debug("  ⚠️  Low liquidity: ATR=%.2f price=%.0f (ATR%%=%.2f%%)",
                     atr, price, atr / price * 100)

    # 3. No trend + no volume: ADX < 15 + vol < 1.0
    if (pd.notna(adx) and adx < 15
            and pd.notna(vol_ratio) and vol_ratio < 1.0):
        check_no_trend = True
        logger.debug("  ⚠️  No trend: ADX=%.1f vol=%.1fx", adx, vol_ratio)

    # 4. False breakout: price up 8%+ with below-average volume
    if (pd.notna(ret_20d) and ret_20d > 8.0
            and pd.notna(vol_ratio) and vol_ratio < 1.0):
        check_false_breakout = True
        logger.debug("  ⚠️  False breakout: ret_20d=%.1f%% vol=%.1fx",
                     ret_20d, vol_ratio)

    # ── Apply adjustments ──────────────────────────────────────────
    downgrade_map = {
        "STRONG_BUY": "BUY",
        "BUY": "WEAK_BUY",
        "WEAK_BUY": "HOLD",
        "HOLD": "SELL",
        "SELL": "SELL",
    }

    # Priority: most severe check wins
    if check_low_liquidity:
        # Low liquidity → everything becomes HOLD (too risky)
        return "HOLD"

    if check_falling_knife:
        if signal in ("STRONG_BUY", "BUY"):
            return downgrade_map.get(signal, signal)
        # For WEAK_BUY/HOLD, falling knife with volume = SELL
        if signal in ("WEAK_BUY", "HOLD"):
            return "SELL"

    if check_no_trend and signal in ("STRONG_BUY", "BUY"):
        return downgrade_map.get(signal, signal)

    if check_false_breakout and signal in ("STRONG_BUY", "BUY", "WEAK_BUY"):
        return downgrade_map.get(signal, signal)

    return signal
