#!/usr/bin/env python3
"""Uji GUARD ENTRY berdasar gap buka (lanjutan temuan biaya eksekusi).
Entry nyata = open besok. Guard kandidat: jangan chase / hindari gap-down.
Metode: walk-forward 2 paruh + bootstrap.
"""
import csv, glob, os, random

CACHE = "/home/yuan/screener/idx_alpha_screener/data/cache_v7"
P = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
COST = 0.4

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

bars = {}
for path in glob.glob(os.path.join(CACHE, "v7_*.csv")):
    code = os.path.basename(path)[3:-7]
    arr = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            o, h, l, c = fnum(r["Open"]), fnum(r["High"]), fnum(r["Low"]), fnum(r["Close"])
            if None not in (o, h, l, c):
                arr.append((r["Date"][:10], o, h, l, c))
    arr.sort()
    bars[code] = arr

data = []  # (date, gap, ret_open)
with open(P, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["finalis"] != "True" or r["vol1"] != "True" or r["regime"] != "RANGING": continue
        a = fnum(r["atrp"]); c0 = fnum(r["close"])
        if not a or a <= 0 or not c0 or r["code"] not in bars: continue
        arr = bars[r["code"]]
        i = next((j for j, b in enumerate(arr) if b[0] == r["date"]), None)
        if i is None: continue
        fwd = arr[i+1: i+21]
        if not fwd: continue
        ent = fwd[0][1]
        gap = (ent / c0 - 1) * 100
        sl = ent * (1 - 1.5*a/100); tp = ent * (1 + 3.0*a/100)
        ret = None
        for (dt, o, h, l, c) in fwd:
            if l <= sl: ret = -1.5*a - COST; break
            if h >= tp: ret = 3.0*a - COST; break
        if ret is None:
            ret = (fwd[-1][4]/ent - 1) * 100 - COST
        data.append((r["date"], gap, ret))

dates = sorted({d for d, _, _ in data}); mid = dates[len(dates)//2]
allr = [r for _, _, r in data]
h1r = [r for d, _, r in data if d < mid]; h2r = [r for d, _, r in data if d >= mid]
ball, b1, b2 = sum(allr)/len(allr), sum(h1r)/len(h1r), sum(h2r)/len(h2r)
print(f"n={len(data)} | baseline (entry-open) ALL {ball:+.2f}% | H1 {b1:+.2f}% | H2 {b2:+.2f}%\n")

def guard(name, fn):
    keep = [(d, r) for d, g, r in data if fn(g)]
    k = [r for _, r in keep]
    k1 = [r for d, r in keep if d < mid]; k2 = [r for d, r in keep if d >= mid]
    e = sum(k)/len(k); e1 = sum(k1)/len(k1); e2 = sum(k2)/len(k2)
    ok = "✅" if (e1 > b1 and e2 > b2 and len(k) >= 700) else ""
    print(f"{name:>28} | n={len(k):>5} ALL {e:+.2f}% | H1 {e1:+.2f}% H2 {e2:+.2f}% {ok}")
    return keep

print("Kandidat kalau KEDUA paruh > baseline & ALL naik.")
guard("skip gap > +3%", lambda g: g <= 3.0)
guard("skip gap < 0", lambda g: g >= 0)
guard("keep 0 <= gap <= +3", lambda g: 0 <= g <= 3.0)
guard("keep -0.5 <= gap <= +3", lambda g: -0.5 <= g <= 3.0)
guard("keep gap <= +2", lambda g: g <= 2.0)

# Bootstrap terbaik (keep 0<=gap<=3)
keep = [(d, r) for d, g, r in data if 0 <= g <= 3.0]
import random as R
R.seed(42); imp = []
for _ in range(3000):
    smp = [data[R.randrange(len(data))] for _ in range(len(data))]
    kk = [r for _, g, r in smp if 0 <= g <= 3.0]
    aa = [r for _, _, r in smp]
    if kk and aa:
        imp.append(sum(kk)/len(kk) - sum(aa)/len(aa))
imp.sort()
print(f"\nBootstrap 'keep 0<=gap<=3': {sum(imp)/len(imp):+.2f} pt, CI90% "
      f"[{imp[int(0.05*len(imp))]:+.2f}, {imp[int(0.95*len(imp))]:+.2f}], P>0={100*sum(1 for x in imp if x>0)/len(imp):.0f}%")
