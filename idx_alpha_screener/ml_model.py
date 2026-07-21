"""
ml_model.py — Logistic Regression Probability Scoring
======================================================
Mengganti heuristic scoring dengan model probabilistic.

Cara kerja:
  1. Load model weights yang sudah di-train (disimpan sebagai JSON)
  2. 8 fitur dari conviction factors → hitung logit → sigmoid → probabilitas
  3. Probabilitas = P(return > 0% dalam 5 hari)
  4. Gate: hanya eksekusi BUY jika P(win) >= threshold

Persamaan: p = 1 / (1 + e^-(b0 + b1*x1 + ... + b8*x8))

Model training ada di train_ml_model.py (dijalankan terpisah).
"""

import os
import json
import math
import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger("ml_model")

# ── Model weights (default — akan di-load dari file) ──
# Format: { "b0": intercept, "b1"... "b8": coefficients }
_model_weights: Optional[dict] = None
_model_threshold: float = 0.99  # DISABLED by default (AUC 0.502 = random)
_model_loaded: bool = False

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_model_weights.json")


def load_model(path: str = None) -> bool:
    """Load model weights dari JSON file."""
    global _model_weights, _model_threshold, _model_loaded
    path = path or MODEL_PATH
    if not os.path.exists(path):
        logger.warning("Model weights tidak ditemukan di %s — pakai default heuristic", path)
        _model_loaded = False
        return False
    try:
        with open(path) as f:
            data = json.load(f)
        _model_weights = data["weights"]
        _model_threshold = data.get("threshold", 0.55)
        _model_loaded = True
        logger.info("ML model loaded dari %s (threshold=%.2f)", path, _model_threshold)
        return True
    except Exception as e:
        logger.warning("Gagal load model: %s — pakai default heuristic", e)
        _model_loaded = False
        return False


def predict_proba(factors: dict) -> float:
    """
    Hitung probabilitas win menggunakan Logistic Regression.

    Parameters
    ----------
    factors : dict dengan keys:
        trend, volume, relative_strength, vwap, rsi, macd, weekly_trend, sr_proximity
        Semua nilai 0-100 (dari conviction factor scores).

    Returns
    -------
    float : probabilitas 0.0 - 1.0
    """
    if not _model_weights:
        # Fallback: logistic approximation pake skor rata-rata
        scores = [v for v in factors.values() if isinstance(v, (int, float))]
        avg = sum(scores) / len(scores) if scores else 50
        # Simple sigmoid around avg=50
        return 1.0 / (1.0 + math.exp(-0.06 * (avg - 50)))

    b0 = _model_weights.get("b0", 0)
    feature_order = ["trend", "volume", "relative_strength", "vwap",
                     "rsi", "macd", "weekly_trend", "sr_proximity"]
    
    logit = b0
    for i, feat in enumerate(feature_order):
        coeff = _model_weights.get(f"b{i+1}", 0)
        val = factors.get(feat, 50)
        logit += coeff * val
    
    # Sigmoid
    prob = 1.0 / (1.0 + math.exp(-logit))
    return round(prob, 4)


def classify(prob: float, threshold: float = None) -> str:
    """
    Konversi probabilitas ke sinyal trading.

    threshold: minimal probabilitas untuk BUY (default dari model)
    """
    th = threshold if threshold is not None else _model_threshold
    
    if prob >= 0.80:       return "STRONG_BUY"
    elif prob >= th:        return "BUY"
    elif prob >= th - 0.10: return "WEAK_BUY"
    elif prob >= th - 0.25: return "HOLD"
    else:                   return "SELL"


def get_model_info() -> dict:
    """Info model untuk display."""
    return {
        "loaded": _model_loaded,
        "threshold": _model_threshold,
        "weights_path": MODEL_PATH if os.path.exists(MODEL_PATH) else None,
    }
