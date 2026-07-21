"""slippage.py — Slippage Model Realistis untuk IDX Alpha Screener v3
==============================================================
Fee seragam 0.4% terlalu optimis — di IDX, small cap spread bisa 1-2%.

Tier:
  LARGE_CAP  (>Rp10T mcap atau harga >5000 dan volume >5M):  0.15% spread
  MID_CAP    (Rp1T-10T mcap atau harga 1000-5000):          0.35% spread
  SMALL_CAP  (<Rp1T mcap atau harga <1000):                  0.80% spread
  MICRO_CAP  (<Rp200M mcap atau harga <200):                 1.50% spread + 0.4% fee

Round-trip = slippage (2× spread) + exchange fee + broker fee
  LARGE:   0.35%  (0.15%×2 + 0.05%)
  MID:     0.75%  (0.35%×2 + 0.05%)
  SMALL:   1.65%  (0.80%×2 + 0.05%)
  MICRO:   3.10%  (1.50%×2 + 0.10%)
"""

import logging
import numpy as np

logger = logging.getLogger("slippage")

# ── Global switch (dieksternal dari config) ──
SLIPPAGE_ENABLED = True

# ── Market Cap Tier definitions ──
MCAP_TIERS = {
    "LARGE": {"min_mcap": 10e12, "label": "Large Cap", "spread_pct": 0.15},
    "MID":   {"min_mcap": 1e12,  "label": "Mid Cap",   "spread_pct": 0.35},
    "SMALL": {"min_mcap": 200e9, "label": "Small Cap",  "spread_pct": 0.80},
    "MICRO": {"min_mcap": 0,     "label": "Micro Cap",  "spread_pct": 1.50},
}

# ── Fee ──
EXCHANGE_FEE_PCT = 0.05  # IDX: 0.04% sell + 0.01% buy
BROKER_FEE_PCT = 0.05    # flat per round-trip
MICRO_BROKER_FEE_PCT = 0.10  # broker lebih mahal untuk small cap

# Extra fee untuk sell (IDX punya 0.1% selling fee)
SELL_FEE_PCT = 0.10


def get_tier_from_price(price: float) -> str:
    """Tentukan tier dari harga saja (fallback kalau market cap ga ada)."""
    if price >= 5000:
        return "LARGE"
    elif price >= 1000:
        return "MID"
    elif price >= 200:
        return "SMALL"
    else:
        return "MICRO"


def get_tier_from_mcap(mcap: float) -> str:
    """Tentukan tier dari market cap."""
    for tier_name, tier_info in MCAP_TIERS.items():
        if mcap >= tier_info["min_mcap"]:
            return tier_name
    return "MICRO"


def get_slippage_pct(ticker: str = None, price: float = None,
                      volume: float = None, mcap: float = None) -> dict:
    """
    Hitung slippage + fee untuk satu saham.

    Parameters
    ----------
    ticker : str, optional — ticker (untuk logging)
    price : float — harga saham (untuk fallback tier)
    volume : float, optional — volume harian (untuk adjust)
    mcap : float, optional — market cap (untuk tier utama)

    Returns
    -------
    dict: {tier, spread_pct, fee_pct, total_roundtrip_pct,
           description, slippage_buy, slippage_sell}
    """
    # 1. Determine tier
    if mcap and mcap > 0:
        tier_name = get_tier_from_mcap(mcap)
    elif price and price > 0:
        tier_name = get_tier_from_price(price)
    else:
        tier_name = "MID"  # default aman

    tier_info = MCAP_TIERS[tier_name]
    spread_pct = tier_info["spread_pct"]

    # 2. Adjust spread based on volume (jika ada data)
    if volume and volume > 0 and tier_name in ("SMALL", "MICRO"):
        # Small cap dengan volume rendah → spread lebih lebar
        if volume < 500_000:
            spread_pct *= 1.5
        elif volume < 100_000:
            spread_pct *= 2.5

    # 3. Fee
    brok_fee = BROKER_FEE_PCT if tier_name in ("LARGE", "MID") else MICRO_BROKER_FEE_PCT
    fee_pct = EXCHANGE_FEE_PCT + brok_fee + SELL_FEE_PCT

    # 4. Total round-trip cost: slippage (2× spread) + fee
    # Slippage berlaku di buy (masuk) dan sell (keluar) — 2× spread
    total = (spread_pct * 2) + fee_pct

    return {
        "tier": tier_name,
        "label": tier_info["label"],
        "spread_pct": round(spread_pct, 3),
        "fee_pct": round(fee_pct, 3),
        "total_roundtrip_pct": round(total, 3),
        "slippage_buy_pct": round(spread_pct, 3),
        "slippage_sell_pct": round(spread_pct, 3),
        "description": f"{tier_info['label']} (spread {spread_pct}% + fee {fee_pct}% = {total:.2f}%)",
    }


def get_fee_pct(ticker: str = None, price: float = None,
                volume: float = None, mcap: float = None) -> float:
    """
    Get total round-trip fee + slippage untuk backtest.
    Returns float persentase.
    """
    result = get_slippage_pct(ticker, price, volume, mcap)
    return result["total_roundtrip_pct"]


def estimate_mcap_from_price(price: float, volume: float = None) -> float:
    """
    Estimasi market cap dari harga + volume (approximation).
    Fallback: pakai price-based tier langsung.
    """
    # Default: price-based estimation
    if price >= 5000:
        return 50e12  # Large cap ~Rp50T
    elif price >= 1000:
        return 5e12   # Mid cap ~Rp5T
    elif price >= 200:
        return 500e9  # Small cap ~Rp500M
    else:
        return 100e9  # Micro cap ~Rp100M
