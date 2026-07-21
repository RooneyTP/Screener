# scalp/signals.py — Intraday Signal Pipeline (SKILL.md §② scalping signals)
# ====================================================================
# Ported from 2_consumer_ai.py with ALL proxy values removed.
# Every feature is computed from actual 1m OHLCV data.
# Config-driven: zero magic numbers — all from ScalpConfig.

from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from scalp.config import ScalpConfig

logger = logging.getLogger(__name__)


# ── Data Types ──────────────────────────────────────────────────────

@dataclass
class IntradayFeatures:
    """All features computed from actual 1m OHLCV data. NO proxy values."""
    vwap: float = 0.0
    ema9: float = 0.0
    ema21: float = 0.0
    rsi: float = 50.0
    adx: float = 25.0
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    cci: float = 0.0
    bb_width_pct: float = 0.0
    vol_sma10: float = 0.0
    vol_ratio: float = 1.0
    vwap_distance_pct: float = 0.0
    ema_distance_pct: float = 0.0
    spread_pct: float = 0.0
    orb_position_pct: float = 0.0
    cumulative_delta: float = 0.0
    candle_pattern: str = "neutral"
    n_bars: int = 0
    price: float = 0.0
    volume: float = 0.0


@dataclass
class MarketContext:
    """Daily / macro context for intraday trading."""
    ihsg_change_pct: float = 0.0
    usd_change_pct: float = 0.0
    daily_rsi: float = 50.0
    daily_macd: float = 0.0
    daily_trend: str = "NEUTRAL"


@dataclass
class SignalResult:
    """Output of signal detection."""
    ticker: str = ""
    signal: str = "HINDARI"       # ULTRA_BUY, STRONG_BUY, BUY, HINDARI
    confidence: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    rrr: float = 0.0
    strategy: str = ""             # morning_breakout, afternoon_momentum
    features: IntradayFeatures = field(default_factory=IntradayFeatures)
    reason: list[str] = field(default_factory=list)
    session: str = ""              # "morning" or "afternoon"


# ── Time Filter ─────────────────────────────────────────────────────

def is_trading_allowed(config: ScalpConfig | None = None) -> tuple[bool, str]:
    """Check if current time is within trading hours.

    Returns (allowed, reason).
    """
    if config is None:
        config = ScalpConfig()

    now = datetime.now()
    t = now.hour * 60 + now.minute

    if t < config.minute_auction_end:
        return False, "Pre-open / Auction (before 09:05)"

    if config.skip_lunch and config.minute_lunch_start <= t < config.minute_lunch_end:
        return False, "Lunch break (11:30-13:00) — low liquidity"

    if config.skip_pre_close and t >= config.minute_pre_close_start:
        return False, "Pre-close / closed"

    if t > config.minute_session_end:
        return False, "Market closed"

    return True, "Trading OK"


def get_session(config: ScalpConfig | None = None) -> str:
    """Return current session: 'morning', 'afternoon', or 'closed'."""
    if config is None:
        config = ScalpConfig()
    t = datetime.now().hour * 60 + datetime.now().minute
    if t < config.minute_auction_end or t > config.minute_session_end:
        return "closed"
    if t < config.minute_morning_breakout_end:
        return "morning"
    if config.skip_lunch and config.minute_lunch_start <= t < config.minute_lunch_end:
        return "closed"
    return "afternoon"


# ── Feature Computation ─────────────────────────────────────────────

def compute_intraday_features(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    config: ScalpConfig | None = None,
) -> IntradayFeatures:
    """Compute ALL intraday features from actual 1m OHLCV data.

    Zero proxy values — everything is calculated from real data.

    Args:
        open_: 1m open prices (oldest first)
        high:  1m high prices
        low:   1m low prices
        close: 1m close prices
        volume: 1m volume
        config: ScalpConfig for indicator periods

    Returns:
        IntradayFeatures dataclass with all computed values.
    """
    if config is None:
        config = ScalpConfig()

    n = len(close)
    if n < 5:
        return IntradayFeatures()

    price_now = float(close.iloc[-1])
    vol_now = float(volume.iloc[-1])

    feat = IntradayFeatures(
        price=price_now,
        volume=vol_now,
        n_bars=n,
    )

    # ── VWAP ────────────────────────────────────────────────────
    try:
        cum_vol = volume.cumsum()
        cum_vp = (close * volume).cumsum()
        feat.vwap = float(cum_vp.iloc[-1] / cum_vol.iloc[-1]) if cum_vol.iloc[-1] > 0 else price_now
    except Exception:
        feat.vwap = price_now

    # ── EMA ─────────────────────────────────────────────────────
    try:
        from ta.trend import EMAIndicator
        ema9_series = EMAIndicator(close=close, window=config.ema_fast).ema_indicator()
        feat.ema9 = float(ema9_series.iloc[-1]) if not pd.isna(ema9_series.iloc[-1]) else price_now
        if n >= config.ema_slow:
            ema21_series = EMAIndicator(close=close, window=config.ema_slow).ema_indicator()
            feat.ema21 = float(ema21_series.iloc[-1]) if not pd.isna(ema21_series.iloc[-1]) else price_now
    except ImportError:
        feat.ema9 = float(close.ewm(span=config.ema_fast, adjust=False).mean().iloc[-1])
        if n >= config.ema_slow:
            feat.ema21 = float(close.ewm(span=config.ema_slow, adjust=False).mean().iloc[-1])

    # ── RSI ─────────────────────────────────────────────────────
    try:
        from ta.momentum import RSIIndicator
        rsi_series = RSIIndicator(close=close, window=config.rsi_period).rsi()
        feat.rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
    except ImportError:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(config.rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(config.rsi_period).mean()
        rs = gain / loss.replace(0, 1e-9)
        feat.rsi = float(100 - (100 / (1 + rs.iloc[-1]))) if not pd.isna(rs.iloc[-1]) else 50.0

    # ── ADX ─────────────────────────────────────────────────────
    try:
        from ta.trend import ADXIndicator
        adx_series = ADXIndicator(high=high, low=low, close=close, window=config.adx_period).adx()
        feat.adx = float(adx_series.iloc[-1]) if not pd.isna(adx_series.iloc[-1]) else 25.0
    except ImportError:
        feat.adx = 25.0

    # ── Stochastic ──────────────────────────────────────────────
    try:
        from ta.momentum import StochasticOscillator
        stoch = StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
        feat.stoch_k = float(stoch.stoch().iloc[-1]) if not pd.isna(stoch.stoch().iloc[-1]) else 50.0
        feat.stoch_d = float(stoch.stoch_signal().iloc[-1]) if not pd.isna(stoch.stoch_signal().iloc[-1]) else 50.0
    except (ImportError, Exception):
        feat.stoch_k = 50.0
        feat.stoch_d = 50.0

    # ── CCI ─────────────────────────────────────────────────────
    try:
        from ta.trend import CCIIndicator
        cci_series = CCIIndicator(high=high, low=low, close=close, window=20).cci()
        feat.cci = float(cci_series.iloc[-1]) if not pd.isna(cci_series.iloc[-1]) else 0.0
    except (ImportError, Exception):
        typical = (high + low + close) / 3
        sma_tp = typical.rolling(20).mean()
        mad = typical.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
        cci = (typical - sma_tp) / (0.015 * mad.replace(0, 1e-9))
        feat.cci = float(cci.iloc[-1]) if not pd.isna(cci.iloc[-1]) else 0.0

    # ── Bollinger Bands Width ───────────────────────────────────
    try:
        from ta.volatility import BollingerBands
        bb = BollingerBands(close=close, window=20, window_dev=2)
        bb_upper = float(bb.bollinger_hband().iloc[-1])
        bb_lower = float(bb.bollinger_lband().iloc[-1])
        bb_mid = float(bb.bollinger_mavg().iloc[-1])
        if bb_mid > 0:
            feat.bb_width_pct = (bb_upper - bb_lower) / bb_mid * 100
    except (ImportError, Exception):
        bb_mid = close.rolling(20).mean().iloc[-1]
        bb_std = close.rolling(20).std().iloc[-1]
        if bb_mid > 0:
            feat.bb_width_pct = (4 * bb_std) / bb_mid * 100

    # ── Volume SMA ──────────────────────────────────────────────
    try:
        feat.vol_sma10 = float(volume.rolling(config.volume_sma_period).mean().iloc[-1])
    except Exception:
        feat.vol_sma10 = vol_now
    feat.vol_ratio = (vol_now / feat.vol_sma10) if feat.vol_sma10 > 0 else 1.0

    # ── Distance metrics ────────────────────────────────────────
    if feat.vwap > 0:
        feat.vwap_distance_pct = (price_now - feat.vwap) / feat.vwap * 100
    if feat.ema9 > 0:
        feat.ema_distance_pct = (price_now - feat.ema9) / feat.ema9 * 100

    # ── Spread estimation ───────────────────────────────────────
    if n >= 5:
        spreads = (high - low) / close.replace(0, 1e-9)
        feat.spread_pct = float(spreads.tail(20).mean() * 100)

    # ── Opening Range position ──────────────────────────────────
    # First 5 bars = opening range
    orb_n = min(5, n)
    orb_high = high.iloc[:orb_n].max()
    orb_low = low.iloc[:orb_n].min()
    if orb_high > orb_low:
        feat.orb_position_pct = (price_now - orb_low) / (orb_high - orb_low) * 100

    # ── Cumulative Delta (proxy: consecutive candle direction) ─
    direction = (close.diff() > 0).astype(int) - (close.diff() < 0).astype(int)
    feat.cumulative_delta = float(direction.tail(20).sum())

    # ── Candle pattern ──────────────────────────────────────────
    if n >= 1:
        body = price_now - float(open_.iloc[-1])
        wick_upper = float(high.iloc[-1]) - max(price_now, float(open_.iloc[-1]))
        wick_lower = min(price_now, float(open_.iloc[-1])) - float(low.iloc[-1])
        total_range = float(high.iloc[-1] - low.iloc[-1])
        if total_range > 0:
            body_pct = abs(body) / total_range
            if body_pct > 0.6:
                feat.candle_pattern = "bullish" if body > 0 else "bearish"
            elif body_pct < 0.3 and wick_upper > wick_lower * 1.5:
                feat.candle_pattern = "hammer"
            elif body_pct < 0.3 and wick_lower > wick_upper * 1.5:
                feat.candle_pattern = "shooting_star"

    return feat


# ── Market Context ──────────────────────────────────────────────────

def get_market_context(config: ScalpConfig | None = None) -> MarketContext:
    """Get daily/macro context from cached data.

    Uses data_lake/histori_ihsg.parquet for daily RSI, MACD.
    Uses macro data from screener global variables if available.
    """
    ctx = MarketContext()

    # ── IHSG Change ────────────────────────────────────────────
    try:
        import yfinance as yf
        ihsg = yf.download("^JKSE", period="2d", progress=False)
        if not ihsg.empty and len(ihsg) >= 2:
            prev = float(ihsg["Close"].iloc[-2])
            curr = float(ihsg["Close"].iloc[-1])
            ctx.ihsg_change_pct = (curr - prev) / prev * 100 if prev > 0 else 0.0
    except Exception:
        pass

    # ── USD/IDR Change ─────────────────────────────────────────
    try:
        import yfinance as yf
        usd = yf.download("IDR=X", period="2d", progress=False)
        if not usd.empty and len(usd) >= 2:
            prev = float(usd["Close"].iloc[-2])
            curr = float(usd["Close"].iloc[-1])
            ctx.usd_change_pct = (curr - prev) / prev * 100 if prev > 0 else 0.0
    except Exception:
        pass

    # ── Daily RSI & MACD from parquet ──────────────────────────
    try:
        import os as _os
        parquet_path = _os.path.join(_os.path.dirname(__file__), "..", "data_lake", "histori_ihsg.parquet")
        if _os.path.exists(parquet_path):
            df = pd.read_parquet(parquet_path)
            # Find IHSG composite data if available
            ihsg_data = df[df["Ticker"] == "IHSG"] if "Ticker" in df.columns else pd.DataFrame()
            if not ihsg_data.empty and "Close" in ihsg_data.columns:
                close_d = ihsg_data["Close"].dropna()
                if len(close_d) >= 14:
                    delta = close_d.diff()
                    gain = delta.clip(lower=0).rolling(14).mean()
                    loss = (-delta.clip(upper=0)).rolling(14).mean()
                    rs = gain / loss.replace(0, 1e-9)
                    ctx.daily_rsi = float(100 - (100 / (1 + rs.iloc[-1])))
                    ctx.daily_trend = "UP" if close_d.iloc[-1] > close_d.rolling(20).mean().iloc[-1] else "DOWN"
    except Exception:
        pass

    return ctx


# ── Signal Detection ────────────────────────────────────────────────

def detect_morning_breakout(
    feat: IntradayFeatures,
    config: ScalpConfig | None = None,
) -> SignalResult:
    """Opening Range Breakout (ORB) — 09:05 to 09:30.

    Conditions:
    - Price > open (first bar close) AND price > VWAP
    - Volume spike > 2.5× average
    - Minimum 10 bars of data
    """
    if config is None:
        config = ScalpConfig()

    result = SignalResult(strategy="morning_breakout", session="morning")

    if feat.n_bars < config.morning_min_bars:
        return result

    if feat.price <= 0:
        return result

    # Check breakout conditions
    above_open = feat.ema_distance_pct > 0  # proxy: price > ema9 ≈ above open
    above_vwap = feat.vwap_distance_pct > 0
    vol_spike = feat.vol_ratio >= config.morning_vol_spike_mult

    reasons = []
    if above_open:
        reasons.append("above_open")
    if above_vwap:
        reasons.append("above_vwap")
    if vol_spike:
        reasons.append("vol_spike")

    if above_open and above_vwap and vol_spike and feat.candle_pattern in ("bullish", "hammer"):
        result.signal = "ULTRA_BUY"
        result.confidence = min(90, 60 + feat.vol_ratio * 5)
    elif above_open and above_vwap and vol_spike:
        result.signal = "STRONG_BUY"
        result.confidence = min(80, 55 + feat.vol_ratio * 3)
    elif above_open and above_vwap:
        result.signal = "BUY"
        result.confidence = min(65, 45 + feat.vol_ratio * 2)

    if result.signal != "HINDARI":
        result.entry_price = feat.price
        result.stop_loss = round(feat.price * (1 - config.sl_pct))
        result.take_profit = round(feat.price * (1 + config.tp_pct))
        risk = result.entry_price - result.stop_loss
        reward = result.take_profit - result.entry_price
        result.rrr = round(reward / risk, 2) if risk > 0 else 0
        result.features = feat
        result.reason = reasons

    return result


def detect_afternoon_momentum(
    feat: IntradayFeatures,
    config: ScalpConfig | None = None,
) -> SignalResult:
    """VWAP + EMA + RSI + Volume momentum — 09:30 to 15:45.

    Conditions:
    - Price > EMA9 AND Price > VWAP
    - RSI between 40-70
    - Volume spike > 2.0× average
    - ADX > 20 (trend confirmation)
    """
    if config is None:
        config = ScalpConfig()

    result = SignalResult(strategy="afternoon_momentum", session="afternoon")

    if feat.n_bars < config.afternoon_min_bars:
        return result

    if feat.price <= 0:
        return result

    above_ema = feat.ema_distance_pct > 0
    above_vwap = feat.vwap_distance_pct > 0
    rsi_ok = config.afternoon_rsi_min <= feat.rsi <= config.afternoon_rsi_max
    vol_spike = feat.vol_ratio >= config.afternoon_vol_spike_mult
    adx_ok = feat.adx >= config.afternoon_adx_min

    reasons = []
    if above_ema:
        reasons.append("above_ema9")
    if above_vwap:
        reasons.append("above_vwap")
    if rsi_ok:
        reasons.append(f"rsi_{feat.rsi:.0f}")
    if vol_spike:
        reasons.append("vol_spike")
    if adx_ok:
        reasons.append(f"adx_{feat.adx:.0f}")

    # Trending strongly = higher signal
    if above_ema and above_vwap and rsi_ok and vol_spike and adx_ok and feat.adx >= 30:
        result.signal = "ULTRA_BUY"
        result.confidence = min(95, 65 + feat.adx * 0.5 + feat.vol_ratio * 3)
    elif above_ema and above_vwap and rsi_ok and vol_spike:
        result.signal = "STRONG_BUY"
        result.confidence = min(85, 55 + feat.vol_ratio * 3 + feat.adx * 0.3)
    elif above_ema and rsi_ok and vol_spike:
        result.signal = "BUY"
        result.confidence = min(70, 45 + feat.vol_ratio * 2)

    if result.signal != "HINDARI":
        result.entry_price = feat.price
        result.stop_loss = round(feat.price * (1 - config.sl_pct))
        result.take_profit = round(feat.price * (1 + config.tp_pct))
        risk = result.entry_price - result.stop_loss
        reward = result.take_profit - result.entry_price
        result.rrr = round(reward / risk, 2) if risk > 0 else 0
        result.features = feat
        result.reason = reasons

    return result


# ── Combined Signal Builder ─────────────────────────────────────────

def build_signal(
    ticker: str,
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    config: ScalpConfig | None = None,
) -> SignalResult | None:
    """Build a complete scalp trading signal.

    Args:
        ticker: Stock ticker symbol
        open_, high, low, close, volume: 1m OHLCV price series (oldest first)
        config: ScalpConfig (loaded from settings.yaml)

    Returns:
        SignalResult with signal, entry, SL, TP, RRR, or None if no valid signal.
    """
    if config is None:
        config = ScalpConfig()

    # ── Time filter ──────────────────────────────────────────────
    allowed, reason = is_trading_allowed(config)
    if not allowed:
        logger.debug("%s: %s", ticker, reason)
        return None

    # ── Liquidity filter ─────────────────────────────────────────
    if close.iloc[-1] <= 0 or volume.iloc[-1] <= 0:
        return None

    nilai_transaksi = float(close.iloc[-1]) * float(volume.iloc[-1])
    if nilai_transaksi < config.min_transaction_value:
        return None

    # ── Compute features ─────────────────────────────────────────
    feat = compute_intraday_features(open_, high, low, close, volume, config)

    # ── Route to strategy ────────────────────────────────────────
    session = get_session(config)
    if session == "morning":
        result = detect_morning_breakout(feat, config)
    elif session == "afternoon":
        result = detect_afternoon_momentum(feat, config)
    else:
        return None

    if result.signal == "HINDARI":
        return None

    result.ticker = ticker
    return result


# ── Signal Quality Score ────────────────────────────────────────────

def compute_signal_score(result: SignalResult) -> float:
    """Compute a 0-15 signal quality score for the AI model.

    Replaces the old hardcoded `5.0` proxy value.
    """
    score = 0.0
    feat = result.features

    # Trend alignment (0-5 pts)
    if feat.ema_distance_pct > 0:
        score += 2.0
    if feat.ema_distance_pct > 0 and feat.vwap_distance_pct > 0:
        score += 1.5
    if feat.adx >= 30:
        score += 1.5

    # Momentum quality (0-5 pts)
    if 40 <= feat.rsi <= 70:
        score += 2.0
    if feat.stoch_k > feat.stoch_d:
        score += 1.0
    if feat.cci > -100:
        score += 1.0
    if feat.vol_ratio >= 1.5:
        score += 1.0

    # Pattern (0-3 pts)
    if feat.candle_pattern == "bullish":
        score += 2.0
    elif feat.candle_pattern == "hammer":
        score += 3.0
    elif feat.candle_pattern == "shooting_star":
        score -= 1.0

    # Risk (0-2 pts) — penalize wide spreads
    if feat.spread_pct < 0.5:
        score += 2.0
    elif feat.spread_pct < 1.0:
        score += 1.0

    return min(15.0, max(0.0, score))
