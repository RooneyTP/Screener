# scalp/ai.py — Intraday AI Model for Scalping (SKILL.md §②)
# ================================================================
# Replaces 2_consumer_ai.py hardcoded proxy values with real computed features.
# 8-feature intraday vector → ensemble_model.pkl inference.
# Falls back to heuristic if model unavailable.

from __future__ import annotations

import os
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

from scalp.config import ScalpConfig
from scalp.signals import IntradayFeatures, MarketContext, SignalResult, compute_signal_score

logger = logging.getLogger(__name__)


# ── AI Prediction Result ────────────────────────────────────────────

@dataclass
class AIPrediction:
    """Output from AI model inference."""
    win_prob_pct: float = 50.0
    verdict: str = "WEAK"          # ULTRA BUY, BUY, WEAK
    method: str = "fallback"       # ensemble, fallback, heuristic
    features_used: list[float] = None

    def __post_init__(self):
        if self.features_used is None:
            self.features_used = []


# ── Cache for ensemble model ────────────────────────────────────────

_ensemble_cache: dict | None = None
_ensemble_threshold: float = 0.50


# ── Feature Vector Builder ──────────────────────────────────────────

def build_scalp_feature_vector(
    feat: IntradayFeatures,
    ctx: MarketContext | None = None,
    signal_score: float = 5.0,
    rrr: float = 1.5,
) -> list[float]:
    """Build an 8-feature vector for the AI model from REAL intraday data.

    Features:
      1. rsi_1m           — RSI on 1m timeframe
      2. adx_1m           — ADX trend strength
      3. vwap_distance_pct — Distance from VWAP
      4. volume_ratio      — Current vol / avg vol
      5. ema_distance_pct  — Distance from EMA9
      6. spread_pct        — Average spread (high-low)/close
      7. orb_position_pct  — Position within opening range (0-100)
      8. cumulative_delta  — Net candle direction (proxy order flow)

    Context features (appended if available):
      9.  signal_score     — 0-15 computed quality score
      10. rrr              — Risk/reward ratio
      11. ihsg_change_pct  — IHSG index change (from MarketContext)
      12. usd_change_pct   — USD/IDR change (from MarketContext)
    """
    if ctx is None:
        ctx = MarketContext()

    features = [
        float(np.clip(feat.rsi, 0, 100)),
        float(np.clip(feat.adx, 0, 100)),
        float(np.clip(feat.vwap_distance_pct, -10, 10)),
        float(np.clip(feat.vol_ratio, 0, 20)),
        float(np.clip(feat.ema_distance_pct, -10, 10)),
        float(np.clip(feat.spread_pct, 0, 10)),
        float(np.clip(feat.orb_position_pct, 0, 100)),
        float(np.clip(feat.cumulative_delta, -30, 30)),
    ]

    # Extended features (context)
    features.extend([
        float(np.clip(signal_score, 0, 15)),
        float(np.clip(rrr, 0, 10)),
        float(np.clip(ctx.ihsg_change_pct, -5, 5)),
        float(np.clip(ctx.usd_change_pct, -3, 3)),
    ])

    return features


# ── Ensemble Model Inference ────────────────────────────────────────

def _predict_ensemble(features: list[float], config: ScalpConfig | None = None) -> float:
    """Predict win probability using ensemble_model.pkl.

    Returns win probability 0-100, or -1.0 if model unavailable.
    """
    global _ensemble_cache, _ensemble_threshold

    if config is None:
        config = ScalpConfig()

    model_path = config.ai_model_path
    if not os.path.exists(model_path):
        logger.debug("Ensemble model not found at %s", model_path)
        return -1.0

    try:
        import joblib

        if _ensemble_cache is None:
            bundle = joblib.load(model_path)
            _ensemble_cache = bundle["ensemble"]
            _ensemble_threshold = bundle.get("threshold", 0.50)
            logger.info("Loaded ensemble model v%s (threshold=%.3f)",
                       bundle.get("version", "?"), _ensemble_threshold)

        # Ensemble expects 14 features — pad our 8-feature vector
        # The padding approach: repeat/scale our features to match training shape
        # Model was trained on swing features (14-dim). For scalp, we use:
        #   [signal_score, confidence_proxy, rsi, adx, stoch, cci, bb_width, rrr,
        #    mm_conf, mm_retail, ihsg_change, usd_change, rsi_1d, macd_1d]
        f = features
        padded = [
            f[8],        # signal_score ≈ Skor
            f[0] * 0.7,  # RSI scaled ≈ Confidence%
            f[0],        # RSI
            f[1],        # ADX
            f[0],        # Stoch ≈ RSI proxy
            0.0,         # CCI (not in 8-feat)
            0.0,         # BB_Width (not in 8-feat)
            f[9],        # RRR
            0.0,         # MM_Confidence (N/A intraday)
            0.0,         # MM_vs_Retail (N/A intraday)
            f[10],       # IHSG_Change
            f[11],       # USD_Change
            f[0],        # RSI_1d ≈ 1m RSI proxy
            0.0,         # MACD_1d (N/A intraday)
        ]

        clean = np.nan_to_num(np.array(padded, dtype=float).reshape(1, -1),
                              nan=0.0, posinf=0.0, neginf=0.0)
        proba = _ensemble_cache.predict_proba(clean)[0]
        win_prob = proba[1] * 100 if len(proba) > 1 else proba[0] * 100
        return round(float(win_prob), 2)

    except Exception as e:
        logger.debug("Ensemble prediction failed: %s", e)
        return -1.0


# ── Heuristic Fallback ──────────────────────────────────────────────

def _predict_heuristic(feat: IntradayFeatures, ctx: MarketContext | None = None) -> float:
    """Heuristic win probability based on signal quality when ensemble unavailable.

    Returns win probability 0-100.
    """
    score = 50.0

    # Trend alignment
    if feat.ema_distance_pct > 0:
        score += 5
    if feat.vwap_distance_pct > 0:
        score += 5
    if feat.adx >= 30:
        score += 5
    elif feat.adx >= 25:
        score += 3

    # Momentum
    if 40 <= feat.rsi <= 65:
        score += 8
    elif 30 <= feat.rsi <= 70:
        score += 4

    # Volume
    if feat.vol_ratio >= 2.0:
        score += 10
    elif feat.vol_ratio >= 1.5:
        score += 5

    # Pattern
    if feat.candle_pattern == "bullish":
        score += 5
    elif feat.candle_pattern == "hammer":
        score += 8
    elif feat.candle_pattern == "bearish":
        score -= 5

    # Spread (lower is better)
    if feat.spread_pct > 2.0:
        score -= 8
    elif feat.spread_pct > 1.0:
        score -= 3

    # Macro context
    if ctx and ctx.ihsg_change_pct > 1.0:
        score += 3
    elif ctx and ctx.ihsg_change_pct < -1.0:
        score -= 5

    return min(95, max(5, score))


# ── Main Prediction Function ────────────────────────────────────────

def predict_scalp_signal(
    result: SignalResult,
    config: ScalpConfig | None = None,
) -> AIPrediction:
    """Predict win probability for a scalp signal.

    Uses ensemble_model.pkl if available, falls back to heuristic.

    Args:
        result: SignalResult from scalp.signals (contains IntradayFeatures)
        config: ScalpConfig

    Returns:
        AIPrediction with win_prob_pct and verdict.
    """
    if config is None:
        config = ScalpConfig()

    feat = result.features

    # Compute signal quality score
    signal_score = compute_signal_score(result)

    # Get market context
    ctx = get_market_context_cached()

    # Build feature vector
    features = build_scalp_feature_vector(feat, ctx, signal_score, result.rrr)

    # Try ensemble first
    win_prob = _predict_ensemble(features, config)

    if win_prob < 0:
        # Ensemble not available — use heuristic
        win_prob = _predict_heuristic(feat, ctx)
        method = "heuristic"
    else:
        method = "ensemble"

    # Verdict
    if win_prob >= config.ai_confidence_threshold + 10:
        verdict = "ULTRA BUY"
    elif win_prob >= config.ai_confidence_threshold:
        verdict = "BUY"
    else:
        verdict = "WEAK"

    return AIPrediction(
        win_prob_pct=win_prob,
        verdict=verdict,
        method=method,
        features_used=features,
    )


# ── Market Context Cache ─────────────────────────────────────────────

_market_context_cache: MarketContext | None = None
_context_cache_ts: float = 0.0


def get_market_context_cached(ttl_secs: float = 300.0) -> MarketContext:
    """Get market context with caching (refresh every 5 min)."""
    global _market_context_cache, _context_cache_ts

    import time
    now = time.time()

    if _market_context_cache is None or (now - _context_cache_ts) > ttl_secs:
        from scalp.signals import get_market_context
        _market_context_cache = get_market_context()
        _context_cache_ts = now

    return _market_context_cache


# ── Batch Prediction ────────────────────────────────────────────────

def filter_signals_with_ai(
    signals: list[SignalResult],
    config: ScalpConfig | None = None,
) -> list[tuple[SignalResult, AIPrediction]]:
    """Run AI prediction on a batch of signals, keep only those above threshold.

    Returns list of (SignalResult, AIPrediction) tuples, sorted by win_prob descending.
    """
    if config is None:
        config = ScalpConfig()

    results = []
    for sig in signals:
        pred = predict_scalp_signal(sig, config)
        if pred.win_prob_pct >= config.ai_confidence_threshold:
            results.append((sig, pred))

    results.sort(key=lambda x: x[1].win_prob_pct, reverse=True)
    return results
