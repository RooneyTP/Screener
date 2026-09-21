#!/usr/bin/env python3
"""Walk-forward: apakah keunggulan kandidat TP/SL konsisten di 2 paruh periode?
Plus uji berpasangan (paired diff) TP3/SL2.5 vs TP3/SL1.5 di RANGING.
"""
import csv, math
P = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
COST = 0.4

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

rows = []
with open(P, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["finalis"] == "True" and r["vol1"] == "True" and r["regime"] == "RANGING":
            if fnum(r["atrp"]) and fnum(r["atrp"]) > 0:
                rows.append(r)

dates = sorted({r["date"] for r in rows})
mid = dates[len(dates)//2]
h1 = [r for r in rows if r["date"] < mid]
h2 = [r for r in rows if r["date"] >= mid]
print(f"Split: <{mid} (H1, n={len(h1)}) vs >= (H2, n={len(h2)})\n")

def outcome(r, tp_m, sl_m):
    """Return per-trade net return, atau None kalau data kurang."""
    atrp = fnum(r["atrp"])
    tpd = fnum(r[f"tph{int(round(tp_m*10))}"])
    sld = fnum(r[f"slh{int(round(sl_m*10))}"])
    if atrp is None or tpd is None or sld is None: return None
    tpd, sld = int(tpd), int(sld)
    tp_hit, sl_hit = tpd > 0, sld > 0
    if tp_hit and (not sl_hit or tpd < sld): ret = tp_m*atrp
    elif sl_hit and (not tp_hit or sld < tpd): ret = -sl_m*atrp
    elif tp_hit and sl_hit: ret = -sl_m*atrp
    else:
        r20 = fnum(r["ret20f"])
        if r20 is None: return None
        ret = r20
    return ret - COST

def stats(sub, tp_m, sl_m):
    vals = [v for v in (outcome(r, tp_m, sl_m) for r in sub) if v is not None]
    n = len(vals)
    if n == 0: return None
    mean = sum(vals)/n
    sd = (sum((v-mean)**2 for v in vals)/(n-1))**0.5 if n > 1 else 0
    return n, mean, sd

combos = [(3.0,1.5),(3.0,2.0),(3.0,2.5),(2.0,2.5),(2.0,2.0),(2.5,2.5)]
print(f"{'kombinasi':>12} | {'H1 exp':>8} {'H2 exp':>8} | {'ALL exp':>8} | {'WR':>6}")
for tp_m, sl_m in combos:
    s1, s2, sall = stats(h1,tp_m,sl_m), stats(h2,tp_m,sl_m), stats(rows,tp_m,sl_m)
    if not (s1 and s2 and sall): continue
    # WR hitung sederhana dari ALL (outcome >= 0 → dianggap menang? tidak; pakai menang TP saja)
    # hitung WR = fraksi nilai > 0 kasar (TP-first only)
    tag = "  ← SEKARANG" if (tp_m==3.0 and sl_m==1.5) else ""
    print(f"TP{tp_m}/SL{sl_m:>4} | {s1[1]:>+7.2f}% {s2[1]:>+7.2f}% | {sall[1]:>+7.2f}% |{tag}")

# Uji berpasangan: diff per baris TP3/SL2.5 vs TP3/SL1.5
diffs = []
for r in rows:
    a = outcome(r, 3.0, 2.5); b = outcome(r, 3.0, 1.5)
    if a is not None and b is not None: diffs.append(a-b)
n = len(diffs); mean = sum(diffs)/n
sd = (sum((d-mean)**2 for d in diffs)/(n-1))**0.5
se = sd/math.sqrt(n)
print(f"\nPaired diff (TP3/SL2.5 − TP3/SL1.5), RANGING: n={n} mean={mean:+.2f}% SE={se:.2f}% t={mean/se:+.2f}")
print("(|t|>2 ≈ layak dipercaya; 1-2 = indikasi; <1 = belum bisa disimpulkan)")

# t untuk TP3/SL2.0 vs current juga
diffs2 = []
for r in rows:
    a = outcome(r, 3.0, 2.0); b = outcome(r, 3.0, 1.5)
    if a is not None and b is not None: diffs2.append(a-b)
n2 = len(diffs2); m2 = sum(diffs2)/n2
sd2 = (sum((d-m2)**2 for d in diffs2)/(n2-1))**0.5
se2 = sd2/math.sqrt(n2)
print(f"Paired diff (TP3/SL2.0 − TP3/SL1.5), RANGING: n={n2} mean={m2:+.2f}% SE={se2:.2f}% t={m2/se2:+.2f}")
