"""
v5/dynamic_threshold.py — Adaptive Percentile Thresholds
===========================================================
Bukan threshold fix, tapi dinamis berdasarkan percentile
dari semua skor di scan hari ini.

Logic:
  1. Kumpulin semua skor dari semua saham
  2. Urutkan, hitung percentile
  3. Top X% → STRONG_BUY, selanjutnya → BUY, dst.
  4. Threshold menyesuaikan dengan distribusi skor hari itu

Keuntungan:
  - Selalu dapet sinyal, gak peduli market lagi bear/bull
  - Gak perlu kalibrasi ulang threshold
  - Jumlah sinyal konsisten (misal: selalu top 5% = SB)
"""

import numpy as np
from typing import List


# Semua skor dari scan hari ini
_all_scores: List[float] = []
_all_profiles: List[str] = []


def reset():
    """Reset di awal scan."""
    _all_scores.clear()
    _all_profiles.clear()


def record(score: float, profile: str = "MOMENTUM"):
    """Catat skor untuk kalkulasi percentile."""
    _all_scores.append(score)
    _all_profiles.append(profile)


def get_thresholds(profile: str = None,
                   percentile_sb: float = 5,
                   percentile_buy: float = 15,
                   percentile_wb: float = 30,
                   percentile_hold: float = 50,
                   base_thresholds: list = None) -> list:
    """
    Hitung threshold dinamis berdasarkan percentile.

    Parameters
    ----------
    profile : str — filter per profil (None = semua)
    percentile_sb : float — top X% jadi SB
    percentile_buy : float — top X% jadi BUY (include SB)
    percentile_wb, percentile_hold : float

    Returns
    -------
    list : [SB, BUY, WB, HOLD, SELL] thresholds
    """
    if not _all_scores:
        return base_thresholds or [68, 58, 50, 42, 35]

    # Filter per profil
    scores = _all_scores
    if profile and len(_all_profiles) == len(_all_scores):
        scores = [s for s, p in zip(_all_scores, _all_profiles) if p == profile]

    if len(scores) < 5:
        return base_thresholds or [68, 58, 50, 42, 35]

    arr = np.array(scores)
    sb = float(np.percentile(arr, max(0, 100 - percentile_sb)))
    buy = float(np.percentile(arr, max(0, 100 - percentile_buy)))
    wb = float(np.percentile(arr, max(0, 100 - percentile_wb)))
    hold = float(np.percentile(arr, max(0, 100 - percentile_hold)))

    # Round to 1 decimal
    return [round(sb, 1), round(buy, 1), round(wb, 1), round(hold, 1), round(hold - 5, 1)]
