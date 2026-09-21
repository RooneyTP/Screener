#!/usr/bin/env python3
"""Uji GATE MAKRO: filter berbasis kondisi IHSG saat sinyal keluar.
Subset: finalis & vol1 & RANGING, exit TP3/SL1.5, biaya 0.4%.
Gate kandidat kalau: exp(gate=true) >= baseline DI KEDUA paruh & gate=false jelek.
"""
import csv

P = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
PI = "/home/yuan/screener/idx_alpha_screener/cache/_IHSG_.csv"
COST = 0.4

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

# --- IHSG series ---
ihsg = []
with open(PI, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        d = r["Date"][:10]
        c = fnum(r["Close"])
        if c: ihsg.append((d, c))
ihsg.sort()
idx = {d: i for i, (d, _) in enumerate(ihsg)}
closes = [c for _, c in ihsg]

def ihist_feat(i):
    def ma(n):
        if i + 1 < n: return None
        return sum(closes[i+1-n:i+1]) / n
    out = {}
    c = closes[i]
    m20, m50 = ma(20), ma(50)
    out["above20"] = (m20 is not None) and c > m20
    out["above50"] = (m50 is not None) and c > m50
    out["ret5"] = (c / closes[i-5] - 1) * 100 if i >= 5 else None
    out["ret20"] = (c / closes[i-20] - 1) * 100 if i >= 20 else None
    return out

feat = {d: ihist_feat(i) for i, (d, _) in enumerate(ihsg)}

# --- rows ---
rows = []
with open(P, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["finalis"] == "True" and r["vol1"] == "True" and r["regime"] == "RANGING" and (fnum(r["atrp"]) or 0) > 0:
            if r["date"] in feat:
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
    if not v: return (0, None)
    return (len(v), sum(v)/len(v))

b_all = mexp(rows); b1 = mexp([r for r in rows if r["date"] < mid]); b2 = mexp([r for r in rows if r["date"] >= mid])
print(f"Baseline RANGING (join IHSG): n={b_all[0]} ALL={b_all[1]:+.2f}% | H1 {b1[1]:+.2f}% (n={b1[0]}) | H2 {b2[1]:+.2f}% (n={b2[0]})\n")

gates = [
    ("IHSG > MA20", lambda r: feat[r["date"]]["above20"]),
    ("IHSG > MA50", lambda r: feat[r["date"]]["above50"]),
    ("IHSG ret20 > 0", lambda r: (feat[r["date"]]["ret20"] or -99) > 0),
    ("IHSG ret20 > -5", lambda r: (feat[r["date"]]["ret20"] or -99) > -5),
    ("IHSG ret5 > -2", lambda r: (feat[r["date"]]["ret5"] or -99) > -2),
    ("MA20 & ret20>-5", lambda r: feat[r["date"]]["above20"] and (feat[r["date"]]["ret20"] or -99) > -5),
    ("MA50 & ret5>-2", lambda r: feat[r["date"]]["above50"] and (feat[r["date"]]["ret5"] or -99) > -2),
]

print(f"{'gate':>16} | {'n_yes':>6} {'yes ALL':>8} {'yes H1':>8} {'yes H2':>8} | {'no ALL':>7} {'no H1':>7} | kandidat?")
for name, fn in gates:
    yes = [r for r in rows if fn(r)]
    no = [r for r in rows if not fn(r)]
    a = mexp(yes); y1 = mexp([r for r in yes if r["date"] < mid]); y2 = mexp([r for r in yes if r["date"] >= mid])
    n_ = mexp(no); n1 = mexp([r for r in no if r["date"] < mid])
    if not (a[1] is not None and y1[1] is not None and y2[1] is not None): continue
    ok = ""
    if y1[1] > b1[1] and y2[1] > b2[1] and a[0] >= 700 and n_[1] is not None and n_[1] < b_all[1]:
        ok = "✅ KANDIDAT"
    print(f"{name:>16} | {a[0]:>6} {a[1]:>+7.2f}% {y1[1]:>+7.2f}% {y2[1]:>+7.2f}% | {(n_[1] if n_[1] is not None else 0):>+6.2f}% {(n1[1] if n1[1] is not None else 0):>+6.2f}% | {ok}")

# Distribusi bulanan exp (baseline RANGING) — di mana untung/rugi
from collections import defaultdict
mon = defaultdict(list)
for r in rows:
    v = outcome(r)
    if v is not None: mon[r["date"][:7]].append(v)
print("\nBulanan (baseline RANGING):")
print(", ".join(f"{m}:{sum(v)/len(v):+.1f}%({len(v)})" for m, v in sorted(mon.items())))

# Sebaran state IHSG di H1 vs H2 (apakah gate bedakan periode?)
for label, sub in (("H1", [r for r in rows if r["date"] < mid]), ("H2", [r for r in rows if r["date"] >= mid])):
    a20 = sum(1 for r in sub if feat[r["date"]]["above20"])
    a50 = sum(1 for r in sub if feat[r["date"]]["above50"])
    print(f"{label}: di atas MA20 {100*a20/len(sub):.0f}% · di atas MA50 {100*a50/len(sub):.0f}%")
