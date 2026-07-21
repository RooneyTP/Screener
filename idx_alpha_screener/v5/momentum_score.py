"""
v5/momentum_score.py — Momentum of Score
=========================================
Bukan cuma skor absolut, tapi PERUBAHAN skor dalam 5-10 hari terakhir.
Ide: kenaikan skor = akselerasi momentum yang bisa berlanjut.

Logic:
  - Simpan history skor 10 hari terakhir
  - Hitung delta (hari ini - N hari lalu)
  - Bonus jika skor MENGALAMI KENAIKAN signifikan
  - Malus jika skor turun (momentum hilang)
"""

import numpy as np
import pandas as pd
from typing import Optional

# Cache score history per ticker (in-memory, reset tiap scan)
_score_history: dict = {}


def reset():
    """Reset history — panggil di awal scan."""
    _score_history.clear()


def record(ticker: str, score: float):
    """Catat skor hari ini untuk ticker."""
    if ticker not in _score_history:
        _score_history[ticker] = []
    _score_history[ticker].append(score)
    # Simpan maksimal 20 entry
    if len(_score_history[ticker]) > 20:
        _score_history[ticker] = _score_history[ticker][-20:]


def compute_momentum(ticker: str, current_score: float,
                     days: int = 5, weight: float = 1.3) -> float:
    """
    Hitung momentum score: current_score + bonus perubahan.

    Parameters
    ----------
    ticker : str
    current_score : float — conviction hari ini
    days : int — jumlah hari lookback
    weight : float — pengali perubahan

    Returns
    -------
    float — adjusted score (current + momentum bonus)
    """
    history = _score_history.get(ticker, [])
    if len(history) < days + 1:
        return current_score  # belum ada cukup data

    old_score = history[-days] if len(history) >= days else history[0]
    delta = current_score - old_score

    if delta > 5:
        # Kenaikan signifikan → bonus
        bonus = round(delta * (weight - 1), 1)
        return round(min(current_score + bonus, 100), 1)
    elif delta > 2:
        # Kenaikan moderat → bonus kecil
        bonus = round(delta * 0.15, 1)
        return round(min(current_score + bonus, 100), 1)
    elif delta < -5:
        # Penurunan signifikan → penalty
        penalty = round(abs(delta) * 0.2, 1)
        return round(max(current_score - penalty, 0), 1)
    elif delta < -2:
        # Penurunan moderat → penalty kecil
        penalty = round(abs(delta) * 0.1, 1)
        return round(max(current_score - penalty, 0), 1)
    else:
        return current_score  # stabil


def get_delta(ticker: str, days: int = 5) -> Optional[float]:
    """Ambil delta skor untuk display."""
    history = _score_history.get(ticker, [])
    if len(history) < days + 1:
        return None
    return round(history[-1] - history[-days], 1)
