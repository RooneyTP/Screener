#!/usr/bin/env python3
"""Uji GATE BREADTH: % saham di atas MA50 (dari cache_v7 925 saham) sebagai filter.
Subset & metode sama dengan uji gate sebelumnya (walk-forward 2 paruh + bootstrap).
"""
import csv, glob, os

CACHE = "/home/yuan/screener/idx_alpha_screener/data/cache_v7"
P = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
PI = "/home/yuan/screener/idx_alpha_screener/cache/_IHSG_.csv"
COST = 0.4

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

# ---------- breadth ----------
mas_above = []
for path in glob.glob(os.path.join(CACHE, "v7_*.csv")):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = fnum(r["Close"])
            if c is not None:
                rows.append((r["Date"][:10], c))
    rows.sort()
    closes = [c for _, c in rows]
    s = 0.0
    above = {}
    for i, (d, c) in enumerate(rows):
        s += c
        if i >= 50: s -= closes[i - 50]
        if i >= 49:
            above[d] = c > (s / 50)
    mas_above.append(above)

breadth = {}
for d in sorted({d for a in mas_above for d in a}):
    tot = a = 0
    for above in mas_above:
        v = above.get(d)
        if v is None: continue
        tot += 1
        if v: a += 1
    if tot >= 100:
        breadth[d] = 100.0 * a / tot

print(f"Breadth: {len(breadth)} hari, {len(mas_above)} saham. Contoh: " +
      ", ".join(f"{d}:{breadth[d]:.0f}%" for d in sorted(breadth)[::40]))

# ---------- IHSG MA50 ----------
ihsg = []
with open(PI, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        c = fnum(r["Close"])
        if c: ihsg.append((r["Date"][:10], c))
ihsg.sort(); closes_i = [c for _, c in ihsg]
ihsg_above50 = {d: (i >= 49 and closes_i[i] > sum(closes_i[i-49:i+1])/50) for i, (d, _) in enumerate(ihsg)}

# ---------- rows & outcome ----------
rows = []
with open(P, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["finalis"] == "True" and r["vol1"] == "True" and r["regime"] == "RANGING" and (fnum(r["atrp"]) or 0) > 0:
            if r["date"] in breadth:
                rows.append(r)

def outcome(r):
    atrp = fnum(r["atrp"]); tpd = fnum(r["tph30"]); sld = fnum(r["slh15"])
    if atrp is None or tpd is None or sld is None: return None
    tpd, sld = int(tpd), int(sld)
    tp_hit, sl_hit = tpd > 0, sld > 0
    if tp_hit and (not sl_hit or tpd < sld): ret = 3.0*atrp
    elif sl_hit and (not tp_hit or sld < tpd): ret = -1.5*atrp
    elif tp_hit and sl_hit: ret = -1.5*atrp
    else:
        r20 = fnum(r["ret20f"])
        if r20 is None: return None
        ret = r20
    return ret - COST

def mexp(sub):
    v = [x for x in (outcome(r) for r in sub) if x is not None]
    if not v: return (0, None)
    return (len(v), sum(v)/len(v))

dates = sorted({r["date"] for r in rows}); mid = dates[len(dates)//2]
b_all = mexp(rows); b1 = mexp([r for r in rows if r["date"] < mid]); b2 = mexp([r for r in rows if r["date"] >= mid])
print(f"\nBaseline: n={b_all[0]} ALL={b_all[1]:+.2f}% | H1 {b1[1]:+.2f}% (n={b1[0]}) | H2 {b2[1]:+.2f}% (n={b2[0]})\n")

# Sebaran breadth di H1 vs H2
for label, sub in (("H1", [r for r in rows if r["date"] < mid]), ("H2", [r for r in rows if r["date"] >= mid])):
    bs = [breadth[r["date"]] for r in sub]
    print(f"Breadth rata-rata {label}: {sum(bs)/len(bs):.0f}%")

print(f"\n{'gate':>22} | {'n_yes':>6} {'yesALL':>7} {'yesH1':>7} {'yesH2':>7} | {'noALL':>7} | kandidat?")
def gate_test(name, fn):
    yes = [r for r in rows if fn(r)]
    no = [r for r in rows if not fn(r)]
    a = mexp(yes); y1 = mexp([r for r in yes if r["date"] < mid]); y2 = mexp([r for r in yes if r["date"] >= mid])
    na = mexp(no)
    if not (a[1] is not None and y1[1] is not None and y2[1] is not None): return
    ok = "✅" if (y1[1] > b1[1] and y2[1] > b2[1] and a[0] >= 700) else ""
    print(f"{name:>22} | {a[0]:>6} {a[1]:>+6.2f}% {y1[1]:>+6.2f}% {y2[1]:>+6.2f}% | {(na[1] or 0):>+6.2f}% | {ok}")

for thr in (30, 35, 40, 45, 50):
    gate_test(f"breadth >= {thr}%", lambda r, t=thr: breadth[r["date"]] >= t)
gate_test("breadth >= 40 & IHSG>MA50", lambda r: breadth[r["date"]] >= 40 and ihsg_above50.get(r["date"], False))
gate_test("breadth >= 35 & IHSG>MA50", lambda r: breadth[r["date"]] >= 35 and ihsg_above50.get(r["date"], False))

# Bootstrap untuk kandidat terbaik (breadth >= 40)
import random
pairs = [(outcome(r), breadth[r["date"]] >= 40) for r in rows]
pairs = [(v, g) for v, g in pairs if v is not None]
def stats(data):
    kept = [val for val, g in data if g]
    allv = [val for val, _ in data]
    return sum(kept)/len(kept), sum(allv)/len(allv), len(kept)
mk, ma, nk = stats(pairs)
random.seed(42)
impr = []
for _ in range(3000):
    s = [pairs[random.randrange(len(pairs))] for _ in range(len(pairs))]
    mk_s, ma_s, _ = stats(s)
    impr.append(mk_s - ma_s)
impr.sort()
lo = impr[int(0.05*len(impr))]; hi = impr[int(0.95*len(impr))]
print(f"\nBootstrap breadth>=40: perbaikan {sum(impr)/len(impr):+.2f} pt, CI90% [{lo:+.2f}, {hi:+.2f}], P>0={100*sum(1 for x in impr if x>0)/len(impr):.0f}%")
print(f"(kept n={nk}, exp kept {mk:+.2f}% vs semua {ma:+.2f}%)")

# Cek tumpang-tindih: MA50 saja vs kombinasi breadth&MA50
idx_ma = {i for i, r in enumerate(rows) if ihsg_above50.get(r["date"], False)}
idx_co = {i for i, r in enumerate(rows) if ihsg_above50.get(r["date"], False) and breadth[r["date"]] >= 40}
print(f"\nOverlap: MA50 kept={len(idx_ma)} | kombinasi kept={len(idx_co)} | beda MA50-only={len(idx_ma-idx_co)}, kombinasi-only={len(idx_co-idx_ma)}")
