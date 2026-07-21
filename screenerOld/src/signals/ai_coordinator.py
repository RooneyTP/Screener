# src/signals/ai_coordinator.py — AI Prediction Coordinator
# FIX: Single source of truth for AI predictions across swing & scalping
# Priority: ensemble_model.pkl > ai_swing.joblib > heuristic fallback

import os
import logging
import numpy as np

logger = logging.getLogger("ai_coordinator")

_ensemble_cache = None


def predict_swing(features_14: list[float]) -> tuple[float, str]:
    """
    Predict win probability for swing trading.
    Tries ensemble first, then ai_model fallback, then heuristic.
    
    Returns (win_probability_0_100, method_used).
    """
    global _ensemble_cache

    # 1. Try ensemble model (latih_ai.py output)
    try:
        import joblib
        if os.path.exists("ensemble_model.pkl"):
            if _ensemble_cache is None:
                bundle = joblib.load("ensemble_model.pkl")
                _ensemble_cache = bundle["ensemble"]
                logger.info("[AI] Loaded ensemble model v%s", bundle.get("version", "?"))

            clean = np.nan_to_num(np.array(features_14, dtype=float).reshape(1, -1), nan=0.0, posinf=0.0, neginf=0.0)
            proba = _ensemble_cache.predict_proba(clean)[0]
            win_prob = proba[1] * 100 if len(proba) > 1 else proba[0] * 100
            return round(float(win_prob), 2), "ensemble"
    except Exception as e:
        logger.debug("[AI] Ensemble failed: %s", e)

    # 2. Fallback to ai_model.py MarketAI
    try:
        from ai_model import get_ai_model
        ai = get_ai_model(model_type="swing")
        # Map 14 features → 10 features used by ai_model v4
        features_10 = [features_14[2], features_14[3], 50.0, features_14[7], features_14[8], features_14[9], features_14[10], features_14[11], features_14[13], 0.0]
        win_prob = ai.predict_win_probability(features_10)
        if win_prob > 0:
            return round(float(win_prob), 2), "ai_model_v4"
    except Exception as e:
        logger.debug("[AI] ai_model fallback failed: %s", e)

    # 3. Heuristic last resort
    rsi = features_14[2]
    adx = features_14[3]
    mm_conf = features_14[8] if len(features_14) > 8 else 50
    prob = 40.0
    if 40 <= rsi <= 65: prob += 10
    if adx > 25: prob += 10
    if mm_conf >= 70: prob += 10
    return round(min(85.0, prob), 2), "heuristic"


def ai_verdict(win_prob: float) -> str:
    if win_prob >= 60:
        return "ULTRA BUY"
    elif win_prob >= 50:
        return "BUY"
    return "WEAK"


def predict_scalping(features_14: list[float]) -> tuple[float, str]:
    """Predict win probability for scalping (uses ensemble with proxy features)."""
    return predict_swing(features_14)  # same ensemble, different feature context
