"""Test conviction bonus + lukewarm penalty logic."""
import sys, os, statistics
sys.path.insert(0, os.path.dirname(__file__))
from scoring import compute_total_score, classify, score_rsi, score_macd, score_volume, score_trend, score_volatility, score_vwap, score_stochastic
import pandas as pd

# Test 1: flat profile (all components ~50-65 — lukewarm)
flat = pd.Series({
    'rsi': 52, 'stoch_k': 55, 'stoch_d': 52,
    'macd': 0.5, 'macd_signal': 0.4, 'macd_hist': 0.1, 'ema12': 98,
    'close': 100, 'adx': 20,
    'vol_ratio': 1.1, 'ret_20d': 2.0,
    'atr': 2.0, 'bb_width_pct': 15,
    'pct_vs_vwap': 1.0, 'ema50': 95,
})

# Test 2: spiky profile (volume breakout + VWAP entry)
spiky = pd.Series({
    'rsi': 48, 'stoch_k': 60, 'stoch_d': 45,
    'macd': 1.0, 'macd_signal': 0.5, 'macd_hist': 0.3, 'ema12': 95,
    'close': 100, 'adx': 25,
    'vol_ratio': 1.8, 'ret_20d': 5.0,
    'atr': 2.0, 'bb_width_pct': 10,
    'pct_vs_vwap': 0.5, 'ema50': 90,
})

# Test 3: weak all around
weak = pd.Series({
    'rsi': 28, 'stoch_k': 15, 'stoch_d': 22,
    'macd': -1.0, 'macd_signal': -0.5, 'macd_hist': -0.8, 'ema12': 105,
    'close': 100, 'adx': 12,
    'vol_ratio': 0.5, 'ret_20d': -8.0,
    'atr': 4.0, 'bb_width_pct': 30,
    'pct_vs_vwap': -6.0, 'ema50': 110,
})

# Test 4: really tight flat (all scores within 5 pts)
super_flat = pd.Series({
    'rsi': 55, 'stoch_k': 56, 'stoch_d': 55,
    'macd': 0.3, 'macd_signal': 0.2, 'macd_hist': 0.1, 'ema12': 99,
    'close': 100, 'adx': 18,
    'vol_ratio': 1.0, 'ret_20d': 1.0,
    'atr': 1.5, 'bb_width_pct': 12,
    'pct_vs_vwap': 0.5, 'ema50': 97,
})

print("=" * 70)
print("CONVICTION BONUS + LUKEWARM PENALTY TEST")
print("=" * 70)

for name, row in [("FLAT (lukewarm)", flat), ("SPIKY (conviction)", spiky), 
                   ("WEAK (garbage)", weak), ("SUPER FLAT", super_flat)]:
    scores = [
        score_rsi(row), score_macd(row), score_volume(row),
        score_trend(row), score_volatility(row), score_vwap(row),
        score_stochastic(row)
    ]
    stdev_val = statistics.stdev(scores)
    bonus = stdev_val * 0.10
    penalty = -3 if stdev_val < 10 else 0
    total = compute_total_score(row, "RANGING")
    
    print(f"\n--- {name} ---")
    print(f"  Component scores: {[f'{s:.0f}' for s in scores]}")
    print(f"  STDEV:            {stdev_val:.2f}")
    print(f"  Conviction bonus: +{bonus:.3f}")
    print(f"  Lukewarm penalty: {penalty}")
    print(f"  Total score:      {total:.1f}")
    print(f"  Signal:           {classify(total, 'RANGING')}")

print("\n" + "=" * 70)
print("EXPECTED: Spiky > Flat (conviction > lukewarm)")
print("          Super flat should be pushed DOWN by penalty")
print("=" * 70)
