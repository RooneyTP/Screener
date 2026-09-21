#!/usr/bin/env python3
"""Uji kandidat filter ENTRY di subset RANGING (eksit tetap TP3/SL1.5).
Metode: bandingkan ekspektasi filter vs baseline untuk tiap paruh periode.
Filter 'kandidat' hanya kalau delta positif DI KEDUA paruh (bukan vonis).
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
            if (fnum(r["atrp"]) or 0) > 0:
                rows.append(r)

dates = sorted({r["date"] for r in rows})
mid = dates[len(dates)//2]

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
    if not v: return None
    m = sum(v)/len(v)
    return len(v), m

def F(name, fn):
    return (name, fn)

filters = [
    F("baseline (semua)", lambda r: True),
    F("rsi < 65", lambda r: (fnum(r["rsi"]) or 0) < 65),
    F("rsi < 70", lambda r: (fnum(r["rsi"]) or 0) < 70),
    F("ret20d < 10", lambda r: (fnum(r["ret20d"]) or 0) < 10),
    F("ret20d < 15", lambda r: (fnum(r["ret20d"]) or 0) < 15),
    F("adx >= 20", lambda r: (fnum(r["adx"]) or 0) >= 20),
    F("adx >= 25", lambda r: (fnum(r["adx"]) or 0) >= 25),
    F("weekly BULLISH", lambda r: r["weekly"] == "BULLISH"),
    F("rsi<65 & adx>=20", lambda r: (fnum(r["rsi"]) or 0) < 65 and (fnum(r["adx"]) or 0) >= 20),
]

base_all = mexp(rows); base_h1 = mexp([r for r in rows if r["date"] < mid]); base_h2 = mexp([r for r in rows if r["date"] >= mid])
b_all, b_h1, b_h2 = base_all[1], base_h1[1], base_h2[1]

print(f"Baseline RANGING: n={base_all[0]} exp_ALL={b_all:+.2f}% | H1 {b_h1:+.2f}% (n={base_h1[0]}) | H2 {b_h2:+.2f}% (n={base_h2[0]})")
print(f"(split = {mid})\n")
print(f"{'filter':>18} | {'n':>5} | {'ALL':>7} {'dALL':>7} | {'H1':>7} {'dH1':>7} | {'H2':>7} {'dH2':>7} | kandidat?")
for name, fn in filters:
    sub = [r for r in rows if fn(r)]
    a = mexp(sub); h1 = mexp([r for r in sub if r["date"] < mid]); h2 = mexp([r for r in sub if r["date"] >= mid])
    if not (a and h1 and h2): continue
    d_all, d1, d2 = a[1]-b_all, h1[1]-b_h1, h2[1]-b_h2
    ok = "✅ KANDIDAT" if (d1 > 0 and d2 > 0 and a[0] >= 300) else ""
    print(f"{name:>18} | {a[0]:>5} | {a[1]:>+6.2f}% {d_all:>+6.2f}% | {h1[1]:>+6.2f}% {d1:>+6.2f}% | {h2[1]:>+6.2f}% {d2:>+6.2f}% | {ok}")
