"""
Backtest: compare signal-based vs force-everyday entry.
Baseline: what if we BUY every single day randomly?
"""
import sys, logging, numpy as np, pandas as pd
from collections import defaultdict

sys.path.insert(0, r'C:\Hermes_Workspace\Screener')
logging.basicConfig(level=logging.WARNING)

from idx_alpha_screener.data_invezgo import InvezgoProvider
from idx_alpha_screener.data import compute_all_indicators, align_to_market, fetch_ihsg_cached
from idx_alpha_screener.scoring import compute_total_score, THRESHOLDS
from idx_alpha_screener.regime import detect_market_regime

WATCHLIST = sorted(set(["AKRA","ALII","ASII","BISI","BNBR","BRPT","BUMI","CPIN","CUAN","DSSA",
    "ELTY","ENRG","HMSP","ICBP","INDF","ISAT","KLBF","MPPA","TPIA","UNTR"]))
SIGNAL_LEVELS = {0:"STRONG_BUY",1:"BUY",2:"WEAK_BUY"}

def get_signal_level(score, regime):
    th = THRESHOLDS.get(regime, THRESHOLDS["RANGING"])
    if score >= th[0]: return "STRONG_BUY"
    elif score >= th[1]: return "BUY"
    elif score >= th[2]: return "WEAK_BUY"
    return "HOLD"

ip = InvezgoProvider()
df_ihsg = fetch_ihsg_cached(period="2y")

signal_trades = []   # V4 screener picks
random_trades = []   # every day entry

for ticker in WATCHLIST:
    df = ip.get_historical(ticker, period="2y")
    if df.empty or len(df) < 80: continue
    df = compute_all_indicators(df)
    df = align_to_market(df, df_ihsg=df_ihsg)
    df = df.dropna(subset=["rsi","adx","macd","ema12","ema50"]).copy()
    if len(df) < 80: continue

    for t in range(60, len(df) - 20):
        row = df.iloc[t]
        price = float(row["close"])
        subset = df.iloc[:t+1]
        regime, _, _ = detect_market_regime(subset)
        score = compute_total_score(row, regime)
        signal = get_signal_level(score, regime)

        # Future return at 10 days
        future_price = float(df.iloc[t+10]["close"])
        ret = (future_price - price) / price

        # Random entry = every day
        random_trades.append({"ticker":ticker,"ret":ret,"win":ret>0})

        # Signal-based entry = only signal days
        if signal in ("STRONG_BUY","BUY","WEAK_BUY"):
            signal_trades.append({"ticker":ticker,"signal":signal,"score":score,"regime":regime,"ret":ret,"win":ret>0})

# ── RESULTS ──
print("=" * 70)
print("  KOMPARASI: SIGNAL-BASED vs RANDOM (BUY SETIAP HARI)")
print(f"  Periode: 2025-01 to 2026-07 | Hold 10 hari")
print("=" * 70)

# Random
r_wins = sum(1 for t in random_trades if t["win"])
r_total = len(random_trades)
r_wr = r_wins / r_total * 100
r_avg = np.mean([t["ret"] for t in random_trades]) * 100
r_std = np.std([t["ret"] for t in random_trades]) * 100

print(f"\n📊 BUY SETIAP HARI (Random)")
print(f"  Total entry:  {r_total:,}")
print(f"  Win Rate:     {r_wr:.1f}%")
print(f"  Avg Return:   {r_avg:+.2f}%")
print(f"  Std Dev:      {r_std:.2f}%")

# Signal
s_wins = sum(1 for t in signal_trades if t["win"])
s_total = len(signal_trades)
s_wr = s_wins / s_total * 100
s_avg = np.mean([t["ret"] for t in signal_trades]) * 100
s_std = np.std([t["ret"] for t in signal_trades]) * 100

print(f"\n📈 V4 SCREENER (Signal-based)")
print(f"  Total signal: {s_total:,}")
print(f"  Win Rate:     {s_wr:.1f}%")
print(f"  Avg Return:   {s_avg:+.2f}%")
print(f"  Std Dev:      {s_std:.2f}%")

# Signal breakdown
print(f"\n{'─' * 70}")
print(f"  Breakdown Signal Level (hold 10d)")
print(f"  {'LEVEL':<12} | {'Count':<7} | {'WR':<7} | {'Avg Ret':<9} | {'Better than random?'}")
print(f"{'─' * 70}")

for level in ["STRONG_BUY","BUY","WEAK_BUY"]:
    sl = [t for t in signal_trades if t["signal"] == level]
    if not sl: continue
    lw = sum(1 for t in sl if t["win"])
    lr = np.mean([t["ret"] for t in sl]) * 100
    wr_l = lw / len(sl) * 100
    better = "✅ YES" if wr_l > r_wr else "❌ NO"
    print(f"  {level:<12} | {len(sl):<7} | {wr_l:>5.1f}% | {lr:+>8.2f}% | {better}")

# Regime
print(f"\n{'─' * 70}")
print(f"  By Regime (hold 10d)")
print(f"  {'REGIME':<16} | {'Count':<7} | {'WR':<7} | {'Avg Ret':<9} | {'Better than random?'}")
print(f"{'─' * 70}")

for reg in ["BULL","RANGING","HIGH_VOLATILITY","BEAR"]:
    rg = [t for t in signal_trades if t["regime"] == reg]
    if not rg: continue
    rw = sum(1 for t in rg if t["win"])
    rr = np.mean([t["ret"] for t in rg]) * 100
    wr_r = rw / len(rg) * 100
    better = "✅ YES" if wr_r > r_wr else "❌ NO"
    print(f"  {reg:<16} | {len(rg):<7} | {wr_r:>5.1f}% | {rr:+>8.2f}% | {better}")

# Summary
print(f"\n{'=' * 70}")
print(f"  KESIMPULAN")
print(f"{'=' * 70}")
diff_wr = s_wr - r_wr
diff_avg = s_avg - r_avg
print(f"  Perbedaan WR:       {diff_wr:+.1f}%")
print(f"  Perbedaan Avg Ret:  {diff_avg:+.2f}%")
print(f"  Jumlah sinyal:      {s_total:,} dari {r_total:,} hari ({s_total/r_total*100:.1f}% dari total)")
print(f"")
if diff_wr > 0:
    print(f"  ✅ Screener LEBIH BAIK dari random +{diff_wr:.1f}% WR")
else:
    print(f"  ❌ Screener SAMA atau LEBIH BURUK dari random ({diff_wr:.1f}% WR)")
print(f"{'=' * 70}")
