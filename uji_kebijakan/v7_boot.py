#!/usr/bin/env python3
"""Bootstrap: signifikansi gate 'skip saat IHSG < MA50' (RANGING, TP3/SL1.5)."""
import csv, random

P = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
PI = "/home/yuan/screener/idx_alpha_screener/cache/_IHSG_.csv"
COST = 0.4

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

ihsg = []
with open(PI, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        d = r["Date"][:10]; c = fnum(r["Close"])
        if c: ihsg.append((d, c))
ihsg.sort()
closes = [c for _, c in ihsg]
above50 = {}
for i, (d, _) in enumerate(ihsg):
    above50[d] = i >= 49 and closes[i] > sum(closes[i-49:i+1]) / 50

rows = []
with open(P, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["finalis"] == "True" and r["vol1"] == "True" and r["regime"] == "RANGING" and (fnum(r["atrp"]) or 0) > 0 and r["date"] in above50:
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

pairs = [(outcome(r), above50[r["date"]]) for r in rows]
pairs = [(v, g) for v, g in pairs if v is not None]

def stats(data):
    kept = [v for v, g in data if g]
    allv = [v for v, _ in data]
    mk = sum(kept)/len(kept); ma = sum(allv)/len(allv)
    excl = [v for v, g in data if not g]
    me = sum(excl)/len(excl) if excl else 0.0
    return mk, ma, me, len(kept), len(excl)

mk, ma, me, nk, ne = stats(pairs)
print(f"n={len(pairs)} | kept(di atas MA50)={nk} | excluded={ne}")
print(f"exp kept   = {mk:+.2f}%")
print(f"exp semua  = {ma:+.2f}%  → perbaikan bila skip = {mk-ma:+.2f} pt/trade")
print(f"exp excluded (yang dibuang) = {me:+.2f}%")

random.seed(42)
improve = []
for _ in range(3000):
    sample = [pairs[random.randrange(len(pairs))] for _ in range(len(pairs))]
    mk_s, ma_s, _, _, _ = stats(sample)
    improve.append(mk_s - ma_s)
improve.sort()
lo = improve[int(0.05*len(improve))]; hi = improve[int(0.95*len(improve))]
pos = sum(1 for x in improve if x > 0) / len(improve)
print(f"\nBootstrap 3000×: perbaikan rata-rata {sum(improve)/len(improve):+.2f} pt")
print(f"CI 90%: [{lo:+.2f}, {hi:+.2f}] | P(perbaikan>0) = {100*pos:.0f}%")
print("(kalau CI seluruhnya > 0 → bukti layak; kalau melewati 0 → masih bisa noise)")

# Cek juga di H1 & H2 terpisah
dates = sorted({r["date"] for r in rows}); mid = dates[len(dates)//2]
for label, sub in (("H1", [r for r in rows if r["date"] < mid]), ("H2", [r for r in rows if r["date"] >= mid])):
    pp = [(outcome(r), above50[r["date"]]) for r in sub]; pp = [(v, g) for v, g in pp if v is not None]
    if pp:
        mk_s, ma_s, me_s, nk_s, ne_s = stats(pp)
        print(f"{label}: kept {mk_s:+.2f}% (n={nk_s}) vs semua {ma_s:+.2f}% → {mk_s-ma_s:+.2f} pt | dibuang {me_s:+.2f}% (n={ne_s})")
