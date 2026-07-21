"""Quick logic test for the new scoring."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from scoring import score_rsi, score_macd, score_stochastic, score_volume, score_trend
from scoring import compute_total_score, classify
import pandas as pd

print('=== NEW score_rsi values ===')
for rsi in [25, 32, 38, 42, 48, 52, 58, 62, 68, 75]:
    row_up = pd.Series({'rsi': rsi, 'stoch_k': 55, 'stoch_d': 45})
    row_down = pd.Series({'rsi': rsi, 'stoch_k': 45, 'stoch_d': 55})
    s_up = score_rsi(row_up)
    s_down = score_rsi(row_down)
    print(f'  RSI={rsi:>3}: turning_up={s_up:>3}, turning_down={s_down:>3}')

print()
print('=== NEW score_macd values ===')
for scenario, rd in [
    ('bull_hist+emaOK', {'macd': 1, 'macd_signal': 0.5, 'macd_hist': 0.3, 'ema12': 95, 'close': 100}),
    ('bull_hist-emaBAD', {'macd': 1, 'macd_signal': 0.5, 'macd_hist': 0.3, 'ema12': 105, 'close': 100}),
    ('bull_no_hist',    {'macd': 0.8, 'macd_signal': 0.5, 'macd_hist': -0.1, 'ema12': 95, 'close': 100}),
    ('bear_conf',       {'macd': 0.3, 'macd_signal': 0.5, 'macd_hist': -0.3, 'ema12': 95, 'close': 100}),
    ('bear_hist_pos',   {'macd': 0.3, 'macd_signal': 0.5, 'macd_hist': 0.1, 'ema12': 95, 'close': 100}),
]:
    print(f'  {scenario:>20}: {score_macd(pd.Series(rd)):.0f}')

print()
print('=== Total score ranges by regime ===')
good_row = pd.Series({
    'rsi': 48, 'stoch_k': 60, 'stoch_d': 45,
    'macd': 1.0, 'macd_signal': 0.5, 'macd_hist': 0.3, 'ema12': 95,
    'close': 100, 'adx': 25,
    'vol_ratio': 1.3, 'ret_20d': 3.0,
    'atr': 2.0, 'bb_width_pct': 10,
    'pct_vs_vwap': 1.5, 'ema50': 90,
})
mediocre_row = pd.Series({
    'rsi': 55, 'stoch_k': 55, 'stoch_d': 50,
    'macd': 0.5, 'macd_signal': 0.4, 'macd_hist': 0.1, 'ema12': 98,
    'close': 100, 'adx': 18,
    'vol_ratio': 1.0, 'ret_20d': 0.5,
    'atr': 2.5, 'bb_width_pct': 18,
    'pct_vs_vwap': -1.0, 'ema50': 95,
})
bad_row = pd.Series({
    'rsi': 32, 'stoch_k': 30, 'stoch_d': 40,
    'macd': -0.5, 'macd_signal': 0.0, 'macd_hist': -0.5, 'ema12': 102,
    'close': 100, 'adx': 12,
    'vol_ratio': 0.7, 'ret_20d': -5.0,
    'atr': 3.5, 'bb_width_pct': 30,
    'pct_vs_vwap': -4.5, 'ema50': 105,
})
oversold_row = pd.Series({
    'rsi': 28, 'stoch_k': 15, 'stoch_d': 20,
    'macd': -1.0, 'macd_signal': -0.5, 'macd_hist': -0.5, 'ema12': 105,
    'close': 100, 'adx': 35,
    'vol_ratio': 1.8, 'ret_20d': -8.0,
    'atr': 4.0, 'bb_width_pct': 25,
    'pct_vs_vwap': -6.0, 'ema50': 110,
})
overbought_row = pd.Series({
    'rsi': 72, 'stoch_k': 85, 'stoch_d': 80,
    'macd': 2.0, 'macd_signal': 1.5, 'macd_hist': 1.0, 'ema12': 95,
    'close': 100, 'adx': 40,
    'vol_ratio': 2.0, 'ret_20d': 15.0,
    'atr': 3.0, 'bb_width_pct': 20,
    'pct_vs_vwap': 8.0, 'ema50': 85,
})

for regime in ['BULL', 'BEAR', 'RANGING']:
    print(f'  --- {regime} ---')
    for label, row in [('good', good_row), ('mediocre', mediocre_row), ('bad', bad_row),
                       ('oversold', oversold_row), ('overbought', overbought_row)]:
        score = compute_total_score(row, regime)
        signal = classify(score, regime)
        print(f'    {label:>10}: score={score:>5.1f} → {signal}')

print()
print('=== VERIFICATION: new RSI scores < old RSI scores for oversold? ===')
for rsi in [25, 30, 32, 35, 38, 42, 45, 50, 60, 70]:
    row_up = pd.Series({'rsi': rsi, 'stoch_k': 55, 'stoch_d': 45})
    new_score = score_rsi(row_up)
    # Old logic would have returned 80 for rsi<35+turn, 70 for 35-45, 65 for 45-55, 50 for 55-65, 30 for >65
    if rsi < 35 and rsi <= 35:
        old = 80
    elif rsi < 35:
        old = 45
    elif rsi <= 45:
        old = 70
    elif rsi <= 55:
        old = 65
    elif rsi <= 65:
        old = 50
    else:
        old = 30
    print(f'  RSI={rsi:>3}: new={new_score:>3}, old={old:>3}, diff={new_score-old:>+3}')
